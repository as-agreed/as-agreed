"""Engine adapters (SPEC §5): plain Python here; `adapters.langgraph` and the Temporal stub `adapters.temporal`
import their engine only when used (pip install langgraph, or temporalio)."""
from .base import Adapter, Binding, JsonlSink, MemorySink, Outcome, Sink, StepContext  # noqa: F401
from .plain import PlainAdapter  # noqa: F401
