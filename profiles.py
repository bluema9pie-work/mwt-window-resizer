"""Versioned, atomic storage for per-program window settings (no GUI dependencies)."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import ntpath
import os
from pathlib import Path
import tempfile

PROFILE_VERSION = 1
MODES = frozenset({"borderless_fs", "windowed_fs", "free"})
ProfileKey = tuple[str, str]


def profile_key(exe_path: str, class_name: str) -> ProfileKey:
    """Match a full executable path and window class, independent of title/PID."""
    return ntpath.normcase(ntpath.normpath(exe_path)), class_name.casefold()


def default_profile_path() -> Path:
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    return base / "MWT" / "profiles.json"


@dataclass(frozen=True)
class WindowProfile:
    name: str
    exe_path: str
    class_name: str
    mode: str
    keep_ratio: bool = False
    cover_taskbar: bool = False
    watch: bool = False
    auto_apply: bool = True
    client_size: tuple[int, int] = (800, 600)
    position: tuple[int, int] = (0, 0)

    @property
    def key(self) -> ProfileKey:
        return profile_key(self.exe_path, self.class_name)

    @classmethod
    def from_dict(cls, data: object) -> WindowProfile:
        if not isinstance(data, dict):
            raise ValueError("設定項目必須是物件")
        for field in ("name", "exe_path", "class_name"):
            if not isinstance(data.get(field), str) or not data[field].strip():
                raise ValueError(f"設定缺少有效的 {field}")
        if not ntpath.isabs(data["exe_path"]):
            raise ValueError("程式路徑必須是完整路徑")
        if not isinstance(data.get("mode"), str) or data["mode"] not in MODES:
            raise ValueError("不支援的顯示模式")
        for field in ("keep_ratio", "cover_taskbar", "watch", "auto_apply"):
            if type(data.get(field)) is not bool:
                raise ValueError(f"{field} 必須是布林值")
        pairs = {}
        for field in ("client_size", "position"):
            pair = data.get(field)
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(f"{field} 必須包含兩個整數")
            if any(type(value) is not int for value in pair):
                raise ValueError(f"{field} 必須包含兩個整數")
            if field == "client_size" and any(not 1 <= value <= 32768 for value in pair):
                raise ValueError("視窗尺寸超出有效範圍")
            if field == "position" and any(abs(value) > 2147483647 for value in pair):
                raise ValueError("視窗位置超出有效範圍")
            pairs[field] = tuple(pair)
        return cls(**{field: data[field] for field in (
            "name", "exe_path", "class_name", "mode", "keep_ratio",
            "cover_taskbar", "watch", "auto_apply",
        )}, **pairs)


def load_profiles(path: Path) -> dict[ProfileKey, WindowProfile]:
    try:
        contents = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}
    document = json.loads(contents)
    if (not isinstance(document, dict)
            or type(document.get("version")) is not int
            or document["version"] != PROFILE_VERSION):
        raise ValueError("不支援的設定檔版本")
    entries = document.get("profiles")
    if not isinstance(entries, list):
        raise ValueError("設定檔缺少設定清單")
    profiles = {}
    for entry in entries:
        profile = WindowProfile.from_dict(entry)
        if profile.key in profiles:
            raise ValueError("設定檔包含重複的程式與視窗類別")
        profiles[profile.key] = profile
    return profiles


def save_profiles(path: Path, profiles: dict[ProfileKey, WindowProfile]) -> None:
    """Replace only after a complete, validated UTF-8 document has been flushed."""
    entries = [asdict(WindowProfile.from_dict(asdict(item))) for item in profiles.values()]
    document = json.dumps(
        {"version": PROFILE_VERSION, "profiles": entries}, ensure_ascii=False, indent=2,
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix="profiles-",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(document)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
