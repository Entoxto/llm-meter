"""Small, Qt-independent image store for chat attachments."""
from __future__ import annotations

import base64
from pathlib import Path
from uuid import UUID, uuid4
import zlib

from model_studio.platform.paths import data_dir


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGES = 4
MAX_PIXELS = 16_000_000


class AttachmentError(ValueError):
    pass


def _dimensions(width: int, height: int) -> tuple[int, int]:
    if width < 1 or height < 1 or width * height > MAX_PIXELS:
        raise AttachmentError("Размеры изображения не поддерживаются (максимум 16 Мп).")
    return width, height


def _png(raw: bytes) -> tuple[str, str]:
    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise AttachmentError("Поддерживаются только PNG и JPEG.")
    index = 8
    dimensions = None
    has_data = False
    ended = False
    while index + 12 <= len(raw):
        length = int.from_bytes(raw[index:index + 4], "big")
        kind = raw[index + 4:index + 8]
        end = index + 12 + length
        if end > len(raw):
            raise AttachmentError("PNG повреждён или обрезан.")
        payload = raw[index + 8:index + 8 + length]
        crc = int.from_bytes(raw[index + 8 + length:end], "big")
        if zlib.crc32(kind + payload) & 0xffffffff != crc:
            raise AttachmentError("PNG повреждён (контрольная сумма).")
        if index == 8:
            if kind != b"IHDR" or length != 13:
                raise AttachmentError("PNG повреждён (заголовок).")
            dimensions = _dimensions(int.from_bytes(payload[:4], "big"),
                                     int.from_bytes(payload[4:8], "big"))
        if kind == b"IDAT":
            has_data = True
        if kind == b"IEND":
            ended = True
            if length or end != len(raw):
                raise AttachmentError("PNG повреждён (конец файла).")
            break
        index = end
    if not dimensions or not has_data or not ended:
        raise AttachmentError("PNG повреждён или обрезан.")
    return "image/png", ".png"


def _jpeg(raw: bytes) -> tuple[str, str]:
    if not raw.startswith(b"\xff\xd8") or not raw.endswith(b"\xff\xd9"):
        raise AttachmentError("JPEG повреждён или обрезан.")
    index = 2
    dimensions = None
    while index + 4 <= len(raw):
        if raw[index] != 0xff:
            raise AttachmentError("JPEG повреждён (маркер).")
        while index < len(raw) and raw[index] == 0xff:
            index += 1
        if index >= len(raw):
            break
        marker = raw[index]
        index += 1
        if marker in (0xd8, 0xd9) or 0xd0 <= marker <= 0xd7:
            continue
        if index + 2 > len(raw):
            break
        length = int.from_bytes(raw[index:index + 2], "big")
        if length < 2 or index + length > len(raw):
            raise AttachmentError("JPEG повреждён или обрезан.")
        if marker in (0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7,
                      0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf):
            if length < 7:
                raise AttachmentError("JPEG повреждён (размеры).")
            height = int.from_bytes(raw[index + 3:index + 5], "big")
            width = int.from_bytes(raw[index + 5:index + 7], "big")
            dimensions = _dimensions(width, height)
        if marker == 0xda:
            break
        index += length
    if not dimensions:
        raise AttachmentError("JPEG не содержит корректных размеров.")
    return "image/jpeg", ".jpg"


def inspect_image(raw: bytes) -> tuple[str, str]:
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise AttachmentError("Изображение должно быть не больше 10 МиБ.")
    if raw.startswith(b"\x89PNG"):
        return _png(raw)
    if raw.startswith(b"\xff\xd8"):
        return _jpeg(raw)
    raise AttachmentError("Поддерживаются только PNG и JPEG.")


def _root(root: str | Path | None) -> Path:
    return Path(root).expanduser().resolve() if root is not None else data_dir()


def import_image(source: str | Path, root: str | Path | None = None) -> dict:
    """Copy a supported image to <root>/attachments; never alter the source."""
    source = Path(source).expanduser().resolve(strict=True)
    if not source.is_file():
        raise AttachmentError("Выбранный файл изображения недоступен.")
    with source.open("rb") as handle:
        raw = handle.read(MAX_IMAGE_BYTES + 1)
    mime, suffix = inspect_image(raw)
    folder = _root(root) / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    image_id = str(uuid4())
    target = folder / (image_id + suffix)
    with target.open("xb") as handle:
        handle.write(raw)
    return {"id": image_id, "path": str(target), "name": source.name,
            "mime": mime, "size": len(raw)}


def reference(attachment: dict) -> dict:
    """Return a portable database reference; reject invented paths."""
    try:
        image_id = str(UUID(str(attachment["id"])))
        mime = str(attachment["mime"])
        suffix = {"image/png": ".png", "image/jpeg": ".jpg"}[mime]
        size = int(attachment["size"])
    except (KeyError, ValueError, TypeError) as exc:
        raise AttachmentError("Данные вложения повреждены.") from exc
    if size < 1 or size > MAX_IMAGE_BYTES:
        raise AttachmentError("Размер вложения недопустим.")
    relative = f"attachments/{image_id}{suffix}"
    provided = attachment.get("path")
    if provided and not str(provided).replace("\\", "/").endswith(relative):
        raise AttachmentError("Путь вложения не соответствует его идентификатору.")
    return {"id": image_id, "path": relative, "name": str(attachment.get("name") or "image"),
            "mime": mime, "size": size}


def load_image(attachment: dict, root: str | Path | None = None) -> str:
    """Return base64 bytes from an app-owned reference for backend messages."""
    item = reference(attachment)
    file = _root(root) / item["path"]
    if not file.is_file() or file.resolve() != file:
        raise AttachmentError(f"Вложение {item['name']} отсутствует. Добавьте изображение заново.")
    with file.open("rb") as handle:
        raw = handle.read(MAX_IMAGE_BYTES + 1)
    if len(raw) != item["size"]:
        raise AttachmentError(f"Вложение {item['name']} изменилось. Добавьте изображение заново.")
    mime, _ = inspect_image(raw)
    if mime != item["mime"]:
        raise AttachmentError(f"Формат вложения {item['name']} изменился.")
    return base64.b64encode(raw).decode("ascii")
