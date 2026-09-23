"""Page source and binding data. No model-controlled build commands or plugins."""

from pathlib import PurePosixPath
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, PositiveInt, model_validator

RevisionHash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class FunctionBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    function_id: PositiveInt
    release_id: PositiveInt | None = None
    revision_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
        description="The immutable Function revision_id, not its 64-character revision_hash.",
    )

    @model_validator(mode="after")
    def exact_version(self):
        if (self.release_id is None) == (self.revision_id is None):
            raise ValueError("Bind exactly one immutable Function release or draft revision")
        return self


class PageSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    files: dict[str, str] = Field(max_length=50)
    bindings: dict[str, FunctionBinding] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def bounded_workspace(self):
        total = 0
        for name, content in self.files.items():
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or str(path) != name
                or ".." in path.parts
                or "\\" in name
                or len(name) > 200
                or not all(part and part[0] != "." for part in path.parts)
                or path.suffix not in {".tsx", ".ts", ".jsx", ".js", ".css"}
            ):
                raise ValueError("Page files must be relative TS/JS/CSS paths inside the workspace")
            total += len(content.encode("utf-8"))
        if total > 1_000_000:
            raise ValueError("Page sources exceed the 1 MB workspace limit")
        for name in self.bindings:
            if not name.isidentifier() or len(name) > 64:
                raise ValueError("Binding names must be identifiers of at most 64 characters")
        return self


def page_contract():
    return {
        "version": "page-source-v1",
        "entrypoint": "main.tsx: default-export a React component",
        "files": "Relative TS/JS/CSS files. Import ./styles.css for your styles; no generated preview HTML.",
        "imports": ["react", "react/jsx-runtime", "react-dom/client", "recharts", "lucide-react"],
        "components": "React DOM elements, Recharts charts, and lucide-react icons. CSS must be in workspace files.",
        "bindings": "Named references to exact Function release_id or draft revision_id. Draft references are editable work, not publishable dependencies.",
        "data_api": "import { invoke } from '@praxis/page'; await invoke(bindingName, payload). Direct network/host access is not available in the isolated preview.",
        "validation": "Real bundling, immutable dependency checks, and isolated browser checks are distinct. Missing environments are not passes. Browser smoke checks do not prove all business behavior.",
        "publication": "Requires this exact revision's server-side checks; separate approved operation. Editing does not replace a live release.",
    }
