# -*- coding: utf-8 -*-
"""
Сканирование локальной сети для поиска телефона с TTS-сервером.
Использует параллельный ping sweep и проверку порта 8080.
"""
import socket
import subprocess
import concurrent.futures
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger("phone_scanner")


def get_local_network_prefix() -> str:
    """Возвращает префикс локальной сети, например '192.168.0.'"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return ".".join(local_ip.split(".")[:3]) + "."
    except Exception:
        return "192.168.0."


def check_phone_server(ip: str, port: int = 8080, timeout: float = 1.5) -> Optional[Tuple[str, str]]:
    """Проверяет, отвечает ли Phone TTS Server на данном IP."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(timeout)
        result = s.connect_ex((ip, port))
        if result != 0:
            s.close()
            return None

        # Отправляем HTTP health-check
        request = f"GET /health HTTP/1.1\r\nHost: {ip}:{port}\r\nConnection: close\r\n\r\n"
        s.sendall(request.encode())

        response = b""
        while True:
            chunk = s.recv(1024)
            if not chunk:
                break
            response += chunk
            if b"\r\n\r\n" in response:
                break
        s.close()

        text = response.decode("utf-8", errors="ignore")
        if "200 OK" in text and '"ok":true' in text:
            # Извлекаем engine из JSON
            match = re.search(r'"engine":"([^"]+)"', text)
            engine = match.group(1) if match else "Phone TTS"
            return (ip, engine)
    except Exception:
        pass
    return None


def scan_for_phone(port: int = 8080, timeout: float = 1.5, max_workers: int = 50) -> Optional[Tuple[str, str]]:
    """
    Сканирует локальную сеть в поисках Phone TTS Server.
    Возвращает (ip, engine) или None.
    """
    prefix = get_local_network_prefix()
    logger.info(f"[SCAN] Сканирование {prefix}1-254:{port}...")

    ips = [f"{prefix}{i}" for i in range(1, 255)]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(check_phone_server, ip, port, timeout): ip for ip in ips}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            if result:
                logger.info(f"[SCAN] Найден Phone TTS Server: {result[0]} ({result[1]})")
                return result

    logger.info("[SCAN] Phone TTS Server не найден в сети")
    return None
