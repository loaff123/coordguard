"""CoordGuard: bounded coordinate-only structural evidence."""
from .model import Axis, ArraySnapshot, Contract, Label, Metadata, ValidationError
from .budget import Limits
__version__ = '0.2.0'
from .planner import audit
from .matching import correspond
from .verify import verify_correspondence
