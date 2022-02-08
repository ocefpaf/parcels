import numpy as np

from numba.experimental import jitclass
import numba as nb
from parcels.numba.grid.base import BaseGrid, _base_grid_spec
from .statuscodes import GridCode
from parcels.numba.grid.zgrid import BaseZGrid
from parcels.numba.grid.sgrid import BaseSGrid
from numba import njit
from parcels.numba.utils import numba_reshape_34


def _curve_grid_spec():
    return _base_grid_spec() + [
        ("lat", nb.float32[:, :]),
        ("lon", nb.float32[:, :]),
    ]


@njit
def _squeeze2d(x):
    """Particular implementation of np.squeeze, only to 2 dimensions
    which are the first and last dimensions of the array.
    Only works if all dimensions apart from the first and last are 1.
    """
    shp = np.array(x.shape)
    first = shp[shp > 1][0]
    last = shp[shp > 1][-1]
    return x.reshape(first, last)


class CurvilinearGrid(BaseGrid):
    """Base class for a curvilinear grid"""
    __init_base = BaseGrid.__init__

    def __init__(self, lon, lat, time=None, mesh='flat'):
        lon = _squeeze2d(lon)
        lat = _squeeze2d(lat)
        self.__init_base(lon, lat, time, mesh)
        self.xdim = self.lon.shape[1]
        self.ydim = self.lon.shape[0]
        self.tdim = self.time.size

    def get_dlon(self):
        """Get distances between grid points"""
        return self.lon[0, 1:] - self.lon[0, :-1]

    def search_indices(self, x, y, z, ti=-1, time=-1, search2D=False,
                       particle=None, interp_method="linear"):
        """Transfered from original code."""
        if particle is not None:
            try:
                xi = self.xi[particle.id]
                yi = self.yi[particle.id]
            except Exception:
                xi = int(self.xdim / 2) - 1
                yi = int(self.ydim / 2) - 1
        else:
            xi = int(self.xdim / 2) - 1
            yi = int(self.ydim / 2) - 1
        xsi = eta = -1
        invA = np.array([[1, 0, 0, 0],
                         [-1, 1, 0, 0],
                         [-1, 0, 0, 1],
                         [1, -1, 1, -1]]).astype(nb.float64)
        maxIterSearch = 1e6
        it = 0
        tol = 1.e-10
        if not self.zonal_periodic:
            if x < self.lonlat_minmax[0] or x > self.lonlat_minmax[1]:
                if self.lon[0, 0] < self.lon[0, -1]:
                    self.FieldOutOfBoundError(x, y, z)
                elif x < self.lon[0, 0] and x > self.lon[0, -1]:  # This prevents from crashing in [160, -160]
                    self.FieldOutOfBoundError(x, y, z)
        if y < self.lonlat_minmax[2] or y > self.lonlat_minmax[3]:
            self.FieldOutOfBoundError(x, y, z)

        while xsi < -tol or xsi > 1+tol or eta < -tol or eta > 1+tol:
            px, py = self.get_pxy(xi, yi)
            if self.mesh == 'spherical':
                px[0] = px[0]+360 if px[0] < x-225 else px[0]
                px[0] = px[0]-360 if px[0] > x+225 else px[0]
                px[1:] = np.where(px[1:] - px[0] > 180, px[1:]-360, px[1:])
                px[1:] = np.where(-px[1:] + px[0] > 180, px[1:]+360, px[1:])
            a = np.dot(invA, px)
            b = np.dot(invA, py)

            aa = a[3]*b[2] - a[2]*b[3]
            bb = a[3]*b[0] - a[0]*b[3] + a[1]*b[2] - a[2]*b[1] + x*b[3] - y*a[3]
            cc = a[1]*b[0] - a[0]*b[1] + x*b[1] - y*a[1]
            if abs(aa) < 1e-12:  # Rectilinear cell, or quasi
                eta = -cc / bb
            else:
                det2 = bb*bb-4*aa*cc
                if det2 > 0:  # so, if det is nan we keep the xsi, eta from previous iter
                    det = np.sqrt(det2)
                    eta = (-bb+det)/(2*aa)
            if abs(a[1]+a[3]*eta) < 1e-12:  # this happens when recti cell rotated of 90deg
                xsi = ((y-py[0])/(py[1]-py[0]) + (y-py[3])/(py[2]-py[3])) * .5
            else:
                xsi = (x-a[0]-a[2]*eta) / (a[1]+a[3]*eta)
            if xsi < 0 and eta < 0 and xi == 0 and yi == 0:
                self.FieldOutOfBoundError(x, y, 0)
            if xsi > 1 and eta > 1 and xi == self.xdim-1 and yi == self.ydim-1:
                self.FieldOutOfBoundError(x, y, 0)
            if xsi < -tol:
                xi -= 1
            elif xsi > 1+tol:
                xi += 1
            if eta < -tol:
                yi -= 1
            elif eta > 1+tol:
                yi += 1
            (xi, yi) = self.reconnect_bnd_indices(xi, yi, self.xdim, self.ydim, self.mesh)
            it += 1
            if it > maxIterSearch:
                self.FieldOutOfBoundError(x, y, 0)
        xsi = max(0., xsi)
        eta = max(0., eta)
        xsi = min(1., xsi)
        eta = min(1., eta)

        if self.zdim > 1 and not search2D:
            (zi, zeta) = self.search_indices_vertical(
                x, y, z, xi, yi, xsi, eta, ti, time, interp_method=interp_method)
        else:
            zi = -1
            zeta = 0

        if not ((0 <= xsi <= 1) and (0 <= eta <= 1) and (0 <= zeta <= 1)):
            self.FieldSamplingError(x, y, z)

        if particle is not None:
            self.xi[particle.id] = xi
            self.yi[particle.id] = yi
            self.zi[particle.id] = zi

        return (xsi, eta, zeta, xi, yi, zi)

    def get_pxy(self, xi, yi):
        """Get px and py"""
        px = np.array([self.lon[yi, xi], self.lon[yi, xi+1], self.lon[yi+1, xi+1],
                       self.lon[yi+1, xi]]).astype(nb.float64)
        py = np.array([self.lat[yi, xi], self.lat[yi, xi+1], self.lat[yi+1, xi+1],
                       self.lat[yi+1, xi]]).astype(nb.float64)
        return px, py

    def reconnect_bnd_indices(self, xi, yi, xdim, ydim, sphere_mesh):
        if xi < 0:
            if sphere_mesh:
                xi = xdim-2
            else:
                xi = 0
        if xi > xdim-2:
            if sphere_mesh:
                xi = 0
            else:
                xi = xdim-2
        if yi < 0:
            yi = 0
        if yi > ydim-2:
            yi = ydim-2
            if sphere_mesh:
                xi = xdim - xi
        return xi, yi

    def add_periodic_halo(self, zonal, meridional, halosize=5):
        """Add a 'halo' to the Grid, through extending the Grid (and lon/lat)
        similarly to the halo created for the Fields

        :param zonal: Create a halo in zonal direction (boolean)
        :param meridional: Create a halo in meridional direction (boolean)
        :param halosize: size of the halo (in grid points). Default is 5 grid points
        """
        if zonal:
            lonshift = self.lon[:, -1] - 2 * self.lon[:, 0] + self.lon[:, 1]
            self.lon = np.concatenate((self.lon[:, -halosize:] - lonshift[:, np.newaxis],
                                       self.lon, self.lon[:, 0:halosize] + lonshift[:, np.newaxis]),
                                      axis=len(self.lon.shape)-1)
            self.lat = np.concatenate((self.lat[:, -halosize:],
                                       self.lat, self.lat[:, 0:halosize]),
                                      axis=len(self.lat.shape)-1)
            self.xdim = self.lon.shape[1]
            self.ydim = self.lat.shape[0]
            self.zonal_periodic = True
            self.zonal_halo = halosize
        if meridional:
            latshift = self.lat[-1, :] - 2 * self.lat[0, :] + self.lat[1, :]
            self.lat = np.concatenate((self.lat[-halosize:, :] - latshift[np.newaxis, :],
                                       self.lat, self.lat[0:halosize, :] + latshift[np.newaxis, :]),
                                      axis=len(self.lat.shape)-2)
            self.lon = np.concatenate((self.lon[-halosize:, :],
                                       self.lon, self.lon[0:halosize, :]),
                                      axis=len(self.lon.shape)-2)
            self.xdim = self.lon.shape[1]
            self.ydim = self.lat.shape[0]
            self.meridional_halo = halosize


@jitclass(spec=_curve_grid_spec()+[("depth", nb.float32[:])])
class CurvilinearZGrid(CurvilinearGrid, BaseZGrid):
    """Curvilinear Z Grid.

    :param lon: 2D array containing the longitude coordinates of the grid
    :param lat: 2D array containing the latitude coordinates of the grid
    :param depth: Vector containing the vertical coordinates of the grid, which are z-coordinates.
           The depth of the different layers is thus constant.
    :param time: Vector containing the time coordinates of the grid
    :param time_origin: Time origin (TimeConverter object) of the time axis
    :param mesh: String indicating the type of mesh coordinates and
           units used during velocity interpolation:

           1. spherical (default): Lat and lon in degree, with a
              correction for zonal velocity U near the poles.
           2. flat: No conversion, lat/lon are assumed to be in m.
    """
    __init__curv = CurvilinearGrid.__init__

    def __init__(self, lon, lat, depth=None, time=None, mesh='flat'):
        self.__init__curv(lon, lat, time, mesh)
        self.gtype = GridCode.CurvilinearZGrid
        self.depth = np.zeros(1, dtype=np.float32) if depth is None else depth
        self.zdim = self.depth.size


@jitclass(spec=_curve_grid_spec()+[("depth", nb.float32[:, :, :, :])])
class CurvilinearSGrid(CurvilinearGrid, BaseSGrid):
    """Curvilinear S Grid.

    :param lon: 2D array containing the longitude coordinates of the grid
    :param lat: 2D array containing the latitude coordinates of the grid
    :param depth: 4D (time-evolving) or 3D (time-independent) array containing the vertical coordinates of the grid,
           which are s-coordinates.
           s-coordinates can be terrain-following (sigma) or iso-density (rho) layers,
           or any generalised vertical discretisation.
           The depth of each node depends then on the horizontal position (lon, lat),
           the number of the layer and the time is depth is a 4D array.
           depth array is either a 4D array[xdim][ydim][zdim][tdim] or a 3D array[xdim][ydim[zdim].
    :param time: Vector containing the time coordinates of the grid
    :param time_origin: Time origin (TimeConverter object) of the time axis
    :param mesh: String indicating the type of mesh coordinates and
           units used during velocity interpolation:

           1. spherical (default): Lat and lon in degree, with a
              correction for zonal velocity U near the poles.
           2. flat: No conversion, lat/lon are assumed to be in m.
    """
    __init__curv = CurvilinearGrid.__init__

    def __init__(self, lon, lat, depth, time=None, mesh='flat'):
        self.__init__curv(lon, lat, time, mesh)

        self.gtype = GridCode.CurvilinearSGrid
        self.depth = numba_reshape_34(depth).astype(nb.float32)
        self.zdim = self.depth.shape[-3]
        self.z4d = self.depth.shape[0] != 1
        if self.z4d:
            assert self.tdim == self.depth.shape[0] or self.depth.shape[0] == 0, \
                'depth dimension has the wrong format. It should be [tdim, zdim, ydim, xdim]'
        assert self.xdim == self.depth.shape[-1] or self.depth.shape[-1] == 0, \
            'depth dimension has the wrong format. It should be [tdim, zdim, ydim, xdim]'
        assert self.ydim == self.depth.shape[-2] or self.depth.shape[-2] == 0, \
            'depth dimension has the wrong format. It should be [tdim, zdim, ydim, xdim]'