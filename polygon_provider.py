from .base import DataProvider
from .demo_provider import DemoProvider
from .fmp_provider import FMPProvider
from .polygon_provider import PolygonProvider


def get_provider(name: str) -> DataProvider:
    name = (name or "demo").lower()
    if name == "demo":
        return DemoProvider()
    if name == "fmp":
        return FMPProvider()
    if name == "polygon":
        return PolygonProvider()
    raise ValueError(f"Proveedor desconocido: {name}")
