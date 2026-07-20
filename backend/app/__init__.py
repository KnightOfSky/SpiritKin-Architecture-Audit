"""应用运行时与装配入口。"""

from backend.app.model_provider_context import install_model_provider_action_context_patch

install_model_provider_action_context_patch()

__all__ = ["SpiritKinRuntime", "main"]


def __getattr__(name):
    if name in {"SpiritKinRuntime", "main"}:
        from backend.app.runtime import SpiritKinRuntime, main

        return {"SpiritKinRuntime": SpiritKinRuntime, "main": main}[name]
    raise AttributeError(name)
