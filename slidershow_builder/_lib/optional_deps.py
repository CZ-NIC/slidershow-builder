class MissingOptionalDependency(RuntimeError):
    """Raised at the point of use, not at import time, so installing without an
    extra still lets unrelated commands run."""

    def __init__(self, feature: str, extra: str):
        super().__init__(
            f"{feature} needs an optional dependency. Install it with: pip install slidershow_builder[{extra}]"
        )
