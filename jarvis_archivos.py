"""Archivos y portapapeles entre la PC y la app del celular.

- Carpetas permitidas, por decisión del usuario: **Descargas** y **Documentos** (las
  reales de Windows, aunque se hayan movido: ``SHGetKnownFolderPath``) y los **otros
  discos** (D:, E:…; no el del sistema, que es Windows y programas). Lo que llega del
  celular se guarda en Descargas.
- ``resolver`` impide salir de la carpeta (``..``, rutas absolutas, enlaces).
- El portapapeles se lee y se escribe con la API de Windows (texto Unicode) y solo
  cuando se pide desde el celular o con una orden; no se sincroniza solo.
"""

import os
import re
import time
from pathlib import Path

RAICES = ("descargas", "documentos")  # fijas; los discos se agregan con ``raices()``
NOMBRES = {"descargas": "Descargas", "documentos": "Documentos"}
_OCULTOS = {"desktop.ini", "thumbs.db", "system volume information", "pagefile.sys", "swapfile.sys",
            "hiberfil.sys", "dumpstack.log.tmp"}
MAX_SUBIDA = 4 * 1024**3  # 4 GB
MAX_PORTAPAPELES = 100_000
BLOQUE = 1024 * 1024
_GUID = {
    "descargas": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documentos": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
}
_PROHIBIDOS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVADOS = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)


class ErrorArchivo(ValueError):
    pass


def _discos_extra() -> list:
    """Discos que no son el del sistema (jarvis_system_info.listar_discos)."""
    import jarvis_system_info

    sistema = os.environ.get("SystemDrive", "C:").upper()
    return [d for d in jarvis_system_info.listar_discos() if d.unidad.upper() != sistema]


def raices() -> list[dict]:
    """[{key, name}] de las carpetas permitidas: Descargas, Documentos y los otros discos."""
    lista = [{"key": clave, "name": NOMBRES[clave]} for clave in RAICES]
    for disco in _discos_extra():
        letra = disco.unidad.rstrip(":").upper()
        lista.append({"key": f"disco:{letra}", "name": f"Disco {disco.titulo}"})
    return lista


def es_raiz(raiz: str) -> bool:
    return any(r["key"] == raiz for r in raices()) if raiz.startswith("disco:") else raiz in RAICES


def carpeta(raiz: str) -> Path:
    """Carpeta real de Descargas, Documentos o la raíz de otro disco («disco:D»)."""
    if raiz.startswith("disco:"):
        if not es_raiz(raiz):
            raise ErrorArchivo("Ese disco no está disponible.")
        return Path(raiz.split(":", 1)[1] + ":\\")
    if raiz not in RAICES:
        raise ErrorArchivo("Solo se puede usar Descargas, Documentos o los otros discos.")
    if os.name == "nt":
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        valor = uuid.UUID(_GUID[raiz])
        guid = GUID(valor.fields[0], valor.fields[1], valor.fields[2],
                    (ctypes.c_ubyte * 8).from_buffer_copy(valor.bytes[8:]))
        ruta = ctypes.c_wchar_p()
        shell32 = ctypes.windll.shell32
        if shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(ruta)) == 0:
            try:
                return Path(ruta.value)
            finally:
                ctypes.windll.ole32.CoTaskMemFree(ruta)
    return Path.home() / ("Downloads" if raiz == "descargas" else "Documents")


def resolver(base: Path, relativa: str) -> Path:
    """``base/relativa`` sin salir de ``base`` (ni con ``..`` ni con enlaces)."""
    relativa = (relativa or "").replace("\\", "/").strip("/")
    if relativa and (Path(relativa).is_absolute() or ":" in relativa):
        raise ErrorArchivo("Ruta no válida.")
    base_real = base.resolve()
    destino = (base_real / relativa).resolve()
    if destino != base_real and base_real not in destino.parents:
        raise ErrorArchivo("Esa ruta está fuera de la carpeta permitida.")
    return destino


def listar(base: Path, relativa: str = "") -> list[dict]:
    """Carpetas primero y luego archivos, por nombre: [{name, dir, size, modified}]."""
    carpeta_actual = resolver(base, relativa)
    if not carpeta_actual.is_dir():
        raise ErrorArchivo("Esa carpeta no existe.")
    elementos = []
    try:
        entradas = list(os.scandir(carpeta_actual))
    except PermissionError as error:
        raise ErrorArchivo("Windows no deja abrir esa carpeta.") from error
    for entrada in entradas:
        if entrada.name.startswith((".", "$")) or entrada.name.lower() in _OCULTOS:
            continue  # ocultos del sistema y la papelera ($RECYCLE.BIN)
        try:
            datos = entrada.stat()
            es_carpeta = entrada.is_dir()
        except OSError:
            continue
        elementos.append({"name": entrada.name, "dir": es_carpeta,
                          "size": 0 if es_carpeta else datos.st_size, "modified": int(datos.st_mtime)})
    return sorted(elementos, key=lambda e: (not e["dir"], e["name"].casefold()))


def nombre_seguro(nombre: str) -> str:
    """Nombre de archivo válido en Windows (sin rutas ni caracteres prohibidos)."""
    nombre = os.path.basename((nombre or "").replace("\\", "/"))
    nombre = _PROHIBIDOS.sub("_", nombre).strip(" .")[:150]
    if not nombre or _RESERVADOS.match(nombre):
        nombre = "archivo_del_celular" + (f"_{nombre}" if nombre else "")
    return nombre


def destino_libre(base: Path, nombre: str) -> Path:
    """``nombre`` en ``base`` o, si ya existe, «nombre (1).ext», «nombre (2).ext»…"""
    ruta = base / nombre
    raiz, extension = os.path.splitext(nombre)
    numero = 1
    while ruta.exists():
        ruta = base / f"{raiz} ({numero}){extension}"
        numero += 1
    return ruta


def guardar_subida(origen, largo: int, nombre: str, base: Path | None = None) -> Path:
    """Copia ``largo`` bytes de ``origen`` a Descargas (archivo temporal y luego renombre)."""
    if not 0 < largo <= MAX_SUBIDA:
        raise ErrorArchivo("El archivo está vacío o es demasiado grande (máximo 4 GB).")
    base = base or carpeta("descargas")
    base.mkdir(parents=True, exist_ok=True)
    final = destino_libre(base, nombre_seguro(nombre))
    temporal = final.with_name(final.name + ".jarvis-parcial")
    restante = largo
    try:
        with open(temporal, "wb") as archivo:
            while restante:
                trozo = origen.read(min(BLOQUE, restante))
                if not trozo:
                    raise ErrorArchivo("La transferencia se cortó antes de terminar.")
                archivo.write(trozo)
                restante -= len(trozo)
        final = destino_libre(base, final.name)  # por si apareció otro igual mientras tanto
        temporal.replace(final)
    except BaseException:
        temporal.unlink(missing_ok=True)
        raise
    return final


def tamano_legible(cantidad: int) -> str:
    for unidad in ("B", "KB", "MB", "GB"):
        if cantidad < 1024 or unidad == "GB":
            return f"{cantidad:.0f} {unidad}" if unidad == "B" else f"{cantidad:.1f} {unidad}".replace(".", ",")
        cantidad /= 1024
    return f"{cantidad} B"


# --- Portapapeles (Windows, texto Unicode) ----------------------------------------------

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def _abrir_portapapeles(user32) -> None:
    for _ in range(10):  # otra aplicación puede tenerlo abierto un instante
        if user32.OpenClipboard(None):
            return
        time.sleep(0.05)
    raise OSError("El portapapeles de Windows está ocupado; reintenta.")


def leer_portapapeles() -> str:
    if os.name != "nt":
        raise OSError("El portapapeles solo está disponible en Windows.")
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
    kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
    _abrir_portapapeles(user32)
    try:
        manejador = user32.GetClipboardData(CF_UNICODETEXT)
        if not manejador:
            return ""
        puntero = kernel32.GlobalLock(manejador)
        try:
            return ctypes.wstring_at(puntero)[:MAX_PORTAPAPELES] if puntero else ""
        finally:
            kernel32.GlobalUnlock(manejador)
    finally:
        user32.CloseClipboard()


def escribir_portapapeles(texto: str) -> None:
    if os.name != "nt":
        raise OSError("El portapapeles solo está disponible en Windows.")
    import ctypes
    from ctypes import wintypes

    texto = texto[:MAX_PORTAPAPELES]
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    datos = ctypes.create_unicode_buffer(texto)
    tamano = ctypes.sizeof(datos)
    memoria = kernel32.GlobalAlloc(GMEM_MOVEABLE, tamano)
    if not memoria:
        raise OSError("No hay memoria para copiar al portapapeles.")
    puntero = kernel32.GlobalLock(memoria)
    ctypes.memmove(puntero, datos, tamano)
    kernel32.GlobalUnlock(memoria)
    _abrir_portapapeles(user32)
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(CF_UNICODETEXT, memoria):
            kernel32.GlobalFree(memoria)
            raise OSError("Windows no aceptó el texto en el portapapeles.")
    finally:
        user32.CloseClipboard()
