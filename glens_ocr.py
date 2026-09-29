"""
Glens OCR — Google Lens OCR через protobuf API.

Портировано из overlay-translator (Kotlin -> Python).
Использует официальный Google Lens endpoint для распознавания текста.
"""
import io
import logging
import os
import random
import struct
import requests
from PIL import Image

logger = logging.getLogger(__name__)

_TIMEOUT = 30


class ProtoWriter:
    """Minimal protobuf writer."""

    def __init__(self):
        self._buf = io.BytesIO()

    def write_int32(self, field: int, value: int):
        self._write_tag(field, 0)
        self._write_varint32(value)

    def write_uint64(self, field: int, value: int):
        self._write_tag(field, 0)
        self._write_varint64(value)

    def write_string(self, field: int, value: str):
        self.write_bytes(field, value.encode("utf-8"))

    def write_bytes(self, field: int, value: bytes):
        self._write_tag(field, 2)
        self._write_varint32(len(value))
        self._buf.write(value)

    def write_message(self, field: int, builder):
        inner = ProtoWriter()
        builder(inner)
        self.write_bytes(field, inner.to_bytes())

    def to_bytes(self) -> bytes:
        return self._buf.getvalue()

    def _write_tag(self, field: int, wire: int):
        self._write_varint32((field << 3) | wire)

    def _write_varint32(self, value: int):
        while (value & ~0x7F) != 0:
            self._buf.write(bytes([(value & 0x7F) | 0x80]))
            value >>= 7
        self._buf.write(bytes([value & 0x7F]))

    def _write_varint64(self, value: int):
        while (value & ~0x7F) != 0:
            self._buf.write(bytes([((value & 0x7F) | 0x80) & 0xFF]))
            value >>= 7
        self._buf.write(bytes([value & 0x7F]))


class ProtoReader:
    """Minimal protobuf reader."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def has_remaining(self) -> bool:
        return self._pos < len(self._data)

    def read_tag(self):
        if not self.has_remaining():
            return None
        return self._read_varint32()

    def read_bytes(self) -> bytes:
        length = self._read_varint32()
        if length is None:
            return b""
        safe_len = min(length, len(self._data) - self._pos)
        result = self._data[self._pos:self._pos + safe_len]
        self._pos += safe_len
        return result

    def read_string(self) -> str:
        return self.read_bytes().decode("utf-8", errors="replace")

    def skip_field(self, wire_type: int):
        if wire_type == 0:
            self._read_varint32()
        elif wire_type == 1:
            self._pos += 8
        elif wire_type == 2:
            length = self._read_varint32() or 0
            self._pos += min(length, len(self._data) - self._pos)
        elif wire_type == 5:
            self._pos += 4

    def _read_varint32(self):
        result = 0
        shift = 0
        while shift < 32:
            if not self.has_remaining():
                return None
            b = self._data[self._pos]
            self._pos += 1
            result |= (b & 0x7F) << shift
            if (b & 0x80) == 0:
                return result
            shift += 7
        return result


class GlensOCR:
    """Google Lens OCR через protobuf API."""

    ENDPOINT = "https://lensfrontend-pa.googleapis.com/v1/crupload"
    # Ключ НЕ в коде: берётся из настроек (ocr.online_api_key) или из
    # переменной окружения. Раньше здесь был захардкоженный ключ — он уезжал
    # в репозиторий вместе с исходниками.
    API_KEY = os.environ.get("GOOGLE_LENS_API_KEY", "")
    USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    CLIENT_LANGUAGE = "ja"
    CLIENT_REGION = "Asia/Tokyo"
    MAX_IMAGE_DIMENSION = 1500

    def __init__(self, api_key: str = ""):
        self.api_key = api_key or self.API_KEY

    def recognize(self, image: Image.Image) -> str:
        """Распознаёт текст с изображения через Google Lens."""
        # Масштабируем если нужно
        m = max(image.width, image.height)
        if m > self.MAX_IMAGE_DIMENSION:
            s = self.MAX_IMAGE_DIMENSION / m
            image = image.resize(
                (int(image.width * s), int(image.height * s)),
                Image.Resampling.LANCZOS
            )

        # Конвертируем в PNG
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        png_bytes = buf.getvalue()

        # Строим protobuf запрос
        payload = self._build_request(png_bytes, image.width, image.height)

        # Отправляем запрос
        response_bytes = self._execute_request(payload)

        # Парсим ответ
        return self._extract_text(response_bytes)

    def _build_request(self, png_bytes: bytes, width: int, height: int) -> bytes:
        """Строит protobuf запрос для Google Lens."""
        request_id = random.getrandbits(63)

        writer = ProtoWriter()
        writer.write_message(1, lambda obj_req: self._build_objects_request(
            obj_req, png_bytes, width, height, request_id
        ))
        return writer.to_bytes()

    def _build_objects_request(self, obj_req: ProtoWriter, png_bytes: bytes,
                               width: int, height: int, request_id: int):
        """Строит objects_request."""
        # objects_request_context
        obj_req.write_message(1, lambda ctx: self._build_context(ctx, request_id))

        # image_data
        obj_req.write_message(3, lambda img: self._build_image_data(
            img, png_bytes, width, height
        ))

    def _build_context(self, ctx: ProtoWriter, request_id: int):
        """Строит request_context."""
        # request_id
        ctx.write_message(3, lambda rid: self._build_request_id(rid, request_id))

        # client_context
        ctx.write_message(4, lambda cc: self._build_client_context(cc))

    def _build_request_id(self, rid: ProtoWriter, request_id: int):
        """Строит request_id."""
        rid.write_uint64(1, request_id)
        rid.write_int32(2, 0)
        rid.write_int32(3, 0)
        rid.write_bytes(4, random.randbytes(16))

    def _build_client_context(self, cc: ProtoWriter):
        """Строит client_context."""
        cc.write_int32(1, 3)  # PLATFORM_WEB
        cc.write_int32(2, 4)  # SURFACE_CHROMIUM

        # language_context
        cc.write_message(4, lambda lc: self._build_language_context(lc))

        # filters
        cc.write_message(7, lambda filters: self._build_filters(filters))

    def _build_language_context(self, lc: ProtoWriter):
        """Строит language_context."""
        lc.write_string(1, self.CLIENT_LANGUAGE)
        lc.write_string(2, self.CLIENT_REGION)

    def _build_filters(self, filters: ProtoWriter):
        """Строит filters."""
        filters.write_message(1, lambda fl: fl.write_int32(1, 1))  # AUTO_FILTER

    def _build_image_data(self, img: ProtoWriter, png_bytes: bytes,
                          width: int, height: int):
        """Строит image_data."""
        # png data
        img.write_message(1, lambda p: p.write_bytes(1, png_bytes))

        # dimensions
        img.write_message(3, lambda m: self._build_dimensions(m, width, height))

    def _build_dimensions(self, m: ProtoWriter, width: int, height: int):
        """Строит dimensions."""
        m.write_int32(1, width)
        m.write_int32(2, height)

    def _execute_request(self, payload: bytes) -> bytes:
        """Отправляет запрос в Google Lens."""
        resp = requests.post(
            self.ENDPOINT,
            data=payload,
            headers={
                "Content-Type": "application/x-protobuf",
                "User-Agent": self.USER_AGENT,
                "X-Goog-Api-Key": self.api_key,
            },
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.content

    def _extract_text(self, data: bytes) -> str:
        """Извлекает текст из protobuf ответа."""
        reader = ProtoReader(data)
        all_lines = []

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 2 and wire == 2:
                all_lines.extend(self._parse_objects_response(reader.read_bytes()))
            else:
                reader.skip_field(wire)

        return "\n".join(all_lines).strip()

    def _parse_objects_response(self, data: bytes) -> list:
        """Парсит objects_response."""
        reader = ProtoReader(data)
        lines = []

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 3 and wire == 2:
                lines.extend(self._parse_text(reader.read_bytes()))
            else:
                reader.skip_field(wire)

        return lines

    def _parse_text(self, data: bytes) -> list:
        """Парсит text."""
        reader = ProtoReader(data)
        result = []

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 1 and wire == 2:
                result.extend(self._parse_text_layout(reader.read_bytes()))
            else:
                reader.skip_field(wire)

        return result

    def _parse_text_layout(self, data: bytes) -> list:
        """Парсит text_layout."""
        reader = ProtoReader(data)
        lines = []

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 1 and wire == 2:
                lines.extend(self._parse_paragraph(reader.read_bytes()))
            else:
                reader.skip_field(wire)

        return lines

    def _parse_paragraph(self, data: bytes) -> list:
        """Парсит paragraph."""
        reader = ProtoReader(data)
        lines = []

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 2 and wire == 2:
                lines.append(self._parse_line(reader.read_bytes()))
            else:
                reader.skip_field(wire)

        return lines

    def _parse_line(self, data: bytes) -> str:
        """Парсит line (sequence of words)."""
        reader = ProtoReader(data)
        sb = io.StringIO()

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 1 and wire == 2:
                sb.write(self._parse_word(reader.read_bytes()))
            else:
                reader.skip_field(wire)

        return sb.getvalue()

    def _parse_word(self, data: bytes) -> str:
        """Парсит word (text + separator)."""
        reader = ProtoReader(data)
        text = ""
        separator = ""

        while reader.has_remaining():
            tag = reader.read_tag()
            if tag is None:
                break
            field = tag >> 3
            wire = tag & 0x7

            if field == 2 and wire == 2:
                text = reader.read_string()
            elif field == 3 and wire == 2:
                separator = reader.read_string()
            else:
                reader.skip_field(wire)

        return text + separator
