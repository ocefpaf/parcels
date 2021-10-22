from numba.core.decorators import njit
from numba.core.typing.asnumbatype import as_numba_type
from parcels.numba.grid.field import NumbaField
from parcels.numba.grid.vector_field_2d import NumbaVectorField2D
from parcels.numba.grid.vector_field_3d import NumbaVectorField3D
from numba.experimental import jitclass


@jitclass(spec=[
    ("U", as_numba_type(NumbaField)),
    ("V", as_numba_type(NumbaField)),
    ("W", as_numba_type(NumbaField)),
    ("UV", as_numba_type(NumbaVectorField2D)),
    ("UVW", as_numba_type(NumbaVectorField3D))
])
class NumbaFieldSet():
    def __init__(self, U, V, W=None):
        self.U = U
        self.V = V
        self.UV = NumbaVectorField2D("UV", U, V)
        if W is not None:
            self.W = W
            self.UVW = NumbaVectorField3D("UVW", U, V, W)
