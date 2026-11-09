import os
import unicodedata


def cambiar_volumen_sistema(porcentaje: int) -> None:
    if not 0 <= porcentaje <= 100:
        raise ValueError("El volumen debe estar entre 0 y 100.")
    _operar_volumen_maestro(porcentaje)


def obtener_volumen_sistema() -> int:
    """Devuelve el volumen maestro actual de la salida predeterminada (0-100)."""
    return _operar_volumen_maestro(None)


def _operar_volumen_maestro(porcentaje: int | None) -> int:
    """Establece el volumen si se indica ``porcentaje`` y devuelve el valor actual."""
    if os.name != "nt":
        raise OSError("El control del volumen del sistema solo está disponible en Windows.")

    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(value: str) -> GUID:
        parsed = uuid.UUID(value)
        return GUID.from_buffer_copy(parsed.bytes_le)

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    co_initialize = ole32.CoInitialize
    co_initialize.argtypes = [ctypes.c_void_p]
    co_initialize.restype = ctypes.c_long
    co_uninitialize = ole32.CoUninitialize
    co_uninitialize.argtypes = []
    co_uninitialize.restype = None
    co_create_instance = ole32.CoCreateInstance
    co_create_instance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    co_create_instance.restype = ctypes.c_long

    enumerator = ctypes.c_void_p()
    device = ctypes.c_void_p()
    endpoint = ctypes.c_void_p()
    initialized = False

    def check_hresult(result: int, operation: str) -> None:
        if result < 0:
            raise OSError(
                f"{operation} falló con HRESULT 0x{result & 0xFFFFFFFF:08X}."
            )

    def com_method(
        interface: ctypes.c_void_p,
        index: int,
        result_type,
        *argument_types,
    ):
        vtable = ctypes.cast(
            interface,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        function_type = ctypes.WINFUNCTYPE(
            result_type,
            ctypes.c_void_p,
            *argument_types,
        )
        return function_type(vtable[index])

    def release(interface: ctypes.c_void_p) -> None:
        if interface:
            com_method(interface, 2, wintypes.ULONG)(interface)

    try:
        result = co_initialize(None)
        check_hresult(result, "CoInitialize")
        initialized = result in (0, 1)

        clsid_enumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid_enumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
        result = co_create_instance(
            ctypes.byref(clsid_enumerator),
            None,
            23,
            ctypes.byref(iid_enumerator),
            ctypes.byref(enumerator),
        )
        check_hresult(result, "Crear enumerador de dispositivos de audio")

        result = com_method(
            enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )(enumerator, 0, 0, ctypes.byref(device))
        check_hresult(result, "Obtener dispositivo de reproducción predeterminado")

        iid_endpoint_volume = guid("5CDF2C82-841E-4546-9722-0CF74078229A")
        result = com_method(
            device,
            3,
            ctypes.c_long,
            ctypes.POINTER(GUID),
            wintypes.DWORD,
            ctypes.c_void_p,
            ctypes.POINTER(ctypes.c_void_p),
        )(
            device,
            ctypes.byref(iid_endpoint_volume),
            23,
            None,
            ctypes.byref(endpoint),
        )
        check_hresult(result, "Obtener control de volumen del dispositivo")

        if porcentaje is not None:
            result = com_method(
                endpoint,
                7,
                ctypes.c_long,
                ctypes.c_float,
                ctypes.POINTER(GUID),
            )(endpoint, porcentaje / 100, None)
            check_hresult(result, "Establecer volumen maestro")

        nivel = ctypes.c_float()
        result = com_method(
            endpoint,
            9,
            ctypes.c_long,
            ctypes.POINTER(ctypes.c_float),
        )(endpoint, ctypes.byref(nivel))
        check_hresult(result, "Leer volumen maestro")
        return round(nivel.value * 100)
    finally:
        release(endpoint)
        release(device)
        release(enumerator)
        if initialized:
            co_uninitialize()

class DispositivoNoEncontrado(OSError):
    """El dispositivo de salida pedido no está conectado/activo."""


def dispositivo_presente(dispositivo: str) -> bool:
    """True si la salida (speakers/headphones) está activa en Windows.

    Otros errores (COM, varios dispositivos que coinciden) se propagan para no
    confundirlos con una desconexión.
    """
    try:
        buscar_dispositivo_salida(dispositivo)
    except DispositivoNoEncontrado:
        return False
    return True


def buscar_dispositivo_salida(dispositivo: str) -> tuple[str, str]:
    if os.name != "nt":
        raise OSError("El cambio de dispositivo de salida solo está disponible en Windows.")

    fragments = {
        "speakers": ("high definition audio device",),
        "headphones": ("g533 gaming headset",),
    }
    if dispositivo not in fragments:
        raise ValueError(f"Dispositivo de salida no reconocido: {dispositivo}")

    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT_UNION(ctypes.Union):
        _fields_ = [
            ("pwszVal", ctypes.c_wchar_p),
            ("data", ctypes.c_ubyte * 16),
        ]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [
            ("vt", wintypes.WORD),
            ("wReserved", wintypes.WORD * 3),
            ("value", PROPVARIANT_UNION),
        ]

    def guid(value: str) -> GUID:
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    def com_method(interface, index: int, result_type, *argument_types):
        vtable = ctypes.cast(
            interface,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        function_type = ctypes.WINFUNCTYPE(
            result_type,
            ctypes.c_void_p,
            *argument_types,
        )
        return function_type(vtable[index])

    def check_hresult(result: int, operation: str) -> None:
        if result < 0:
            raise OSError(
                f"{operation} falló con HRESULT 0x{result & 0xFFFFFFFF:08X}."
            )

    def release(interface) -> None:
        if interface:
            com_method(interface, 2, wintypes.ULONG)(interface)

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    ole32.PropVariantClear.argtypes = [ctypes.POINTER(PROPVARIANT)]
    ole32.PropVariantClear.restype = ctypes.c_long

    enumerator = ctypes.c_void_p()
    collection = ctypes.c_void_p()
    policy_config = ctypes.c_void_p()
    initialized = False
    matches: list[tuple[str, str]] = []
    available_names = []

    try:
        result = ole32.CoInitialize(None)
        check_hresult(result, "CoInitialize")
        initialized = result in (0, 1)

        clsid_enumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid_enumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid_enumerator),
            None,
            23,
            ctypes.byref(iid_enumerator),
            ctypes.byref(enumerator),
        )
        check_hresult(result, "Crear enumerador de dispositivos de audio")

        result = com_method(
            enumerator,
            3,
            ctypes.c_long,
            ctypes.c_int,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
        )(enumerator, 0, 1, ctypes.byref(collection))
        check_hresult(result, "Enumerar dispositivos de salida activos")

        count = wintypes.UINT()
        result = com_method(
            collection,
            3,
            ctypes.c_long,
            ctypes.POINTER(wintypes.UINT),
        )(collection, ctypes.byref(count))
        check_hresult(result, "Contar dispositivos de salida")

        friendly_name_key = PROPERTYKEY(
            guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"),
            14,
        )
        device_description_key = PROPERTYKEY(
            guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"),
            2,
        )
        for index in range(count.value):
            device = ctypes.c_void_p()
            property_store = ctypes.c_void_p()
            allocated_id = ctypes.c_wchar_p()
            try:
                result = com_method(
                    collection,
                    4,
                    ctypes.c_long,
                    wintypes.UINT,
                    ctypes.POINTER(ctypes.c_void_p),
                )(collection, index, ctypes.byref(device))
                check_hresult(result, "Obtener dispositivo de salida")

                result = com_method(
                    device,
                    5,
                    ctypes.c_long,
                    ctypes.POINTER(ctypes.c_wchar_p),
                )(device, ctypes.byref(allocated_id))
                check_hresult(result, "Obtener identificador del dispositivo")

                result = com_method(
                    device,
                    4,
                    ctypes.c_long,
                    wintypes.DWORD,
                    ctypes.POINTER(ctypes.c_void_p),
                )(device, 0, ctypes.byref(property_store))
                check_hresult(result, "Leer propiedades del dispositivo")

                names = []
                for property_key in (friendly_name_key, device_description_key):
                    value = PROPVARIANT()
                    result = com_method(
                        property_store,
                        5,
                        ctypes.c_long,
                        ctypes.POINTER(PROPERTYKEY),
                        ctypes.POINTER(PROPVARIANT),
                    )(property_store, ctypes.byref(property_key), ctypes.byref(value))
                    if result >= 0:
                        if value.vt == 31 and value.value.pwszVal:
                            names.append(value.value.pwszVal)
                        ole32.PropVariantClear(ctypes.byref(value))

                label = " ".join(names)
                available_names.append(label)
                normalized_label = unicodedata.normalize("NFKD", label)
                normalized_label = (
                    normalized_label.encode("ascii", "ignore")
                    .decode("ascii")
                    .lower()
                )
                if any(fragment in normalized_label for fragment in fragments[dispositivo]):
                    if allocated_id.value is not None:
                        matches.append((allocated_id.value, names[0] if names else label))
            finally:
                release(property_store)
                if allocated_id:
                    ole32.CoTaskMemFree(ctypes.cast(allocated_id, ctypes.c_void_p))
                release(device)

        if not matches:
            names = ", ".join(available_names) or "ninguno"
            requested = (
                "altavoces High Definition Audio Device"
                if dispositivo == "speakers"
                else "auriculares G533 Gaming Headset"
            )
            raise DispositivoNoEncontrado(
                f"No encontré {requested}. Dispositivos de salida disponibles: {names}."
            )
        if len(matches) > 1:
            names = ", ".join(name for _, name in matches)
            raise OSError(f"Hay varios dispositivos que coinciden: {names}.")
        return matches[0]
    finally:
        release(collection)
        release(enumerator)
        if initialized:
            ole32.CoUninitialize()

def cambiar_dispositivo_salida(dispositivo: str) -> str:
    endpoint_id, endpoint_name = buscar_dispositivo_salida(dispositivo)

    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [
            ("Data1", wintypes.DWORD),
            ("Data2", wintypes.WORD),
            ("Data3", wintypes.WORD),
            ("Data4", ctypes.c_ubyte * 8),
        ]

    def guid(value: str) -> GUID:
        return GUID.from_buffer_copy(uuid.UUID(value).bytes_le)

    def com_method(interface, index: int, result_type, *argument_types):
        vtable = ctypes.cast(
            interface,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)),
        ).contents
        function_type = ctypes.WINFUNCTYPE(
            result_type,
            ctypes.c_void_p,
            *argument_types,
        )
        return function_type(vtable[index])

    def check_hresult(result: int) -> None:
        if result < 0:
            raise OSError(
                "Establecer el dispositivo de salida predeterminado "
                f"falló con HRESULT 0x{result & 0xFFFFFFFF:08X}."
            )

    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    ole32.CoInitialize.argtypes = [ctypes.c_void_p]
    ole32.CoInitialize.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None
    ole32.CoCreateInstance.argtypes = [
        ctypes.POINTER(GUID),
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(GUID),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    ole32.CoCreateInstance.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None

    policy_config = ctypes.c_void_p()
    enumerator = ctypes.c_void_p()
    initialized = False
    try:
        result = ole32.CoInitialize(None)
        check_hresult(result)
        initialized = result in (0, 1)

        clsid_policy_config = guid("294935CE-F637-4E7C-A41B-AB255460B862")
        iid_policy_config = guid("568B9108-44BF-40B4-9006-86AFE5B5A620")
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid_policy_config),
            None,
            23,
            ctypes.byref(iid_policy_config),
            ctypes.byref(policy_config),
        )
        check_hresult(result)

        set_default_endpoint = com_method(
            policy_config,
            12,
            ctypes.c_long,
            ctypes.c_wchar_p,
            ctypes.c_int,
        )
        for role, role_name in (
            (0, "consola"),
            (1, "multimedia"),
            (2, "comunicaciones"),
        ):
            result = set_default_endpoint(policy_config, endpoint_id, role)
            if result < 0:
                raise OSError(
                    f"No pude establecer la salida predeterminada para "
                    f"{role_name} (HRESULT 0x{result & 0xFFFFFFFF:08X})."
                )

        clsid_enumerator = guid("BCDE0395-E52F-467C-8E3D-C4579291692E")
        iid_enumerator = guid("A95664D2-9614-4F35-A746-DE8DB63617E6")
        result = ole32.CoCreateInstance(
            ctypes.byref(clsid_enumerator),
            None,
            23,
            ctypes.byref(iid_enumerator),
            ctypes.byref(enumerator),
        )
        if result < 0:
            raise OSError(
                "No pude verificar la salida de audio predeterminada "
                f"(HRESULT 0x{result & 0xFFFFFFFF:08X})."
            )

        get_default_endpoint = com_method(
            enumerator,
            4,
            ctypes.c_long,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_void_p),
        )
        for role, role_name in (
            (0, "consola"),
            (1, "multimedia"),
            (2, "comunicaciones"),
        ):
            device = ctypes.c_void_p()
            result = get_default_endpoint(enumerator, 0, role, ctypes.byref(device))
            if result < 0:
                raise OSError(
                    f"No pude verificar la salida predeterminada para {role_name} "
                    f"(HRESULT 0x{result & 0xFFFFFFFF:08X})."
                )
            device_id = ctypes.c_wchar_p()
            try:
                result = com_method(
                    device,
                    5,
                    ctypes.c_long,
                    ctypes.POINTER(ctypes.c_wchar_p),
                )(device, ctypes.byref(device_id))
                if result < 0:
                    raise OSError(
                        f"No pude leer el dispositivo predeterminado para {role_name} "
                        f"(HRESULT 0x{result & 0xFFFFFFFF:08X})."
                    )
                if (device_id.value or "").casefold() != endpoint_id.casefold():
                    raise OSError(
                        f"Windows no aplicó {endpoint_name} como salida "
                        f"predeterminada para {role_name}."
                    )
            finally:
                if device_id:
                    ole32.CoTaskMemFree(ctypes.cast(device_id, ctypes.c_void_p))
                com_method(device, 2, wintypes.ULONG)(device)
    finally:
        if enumerator:
            com_method(enumerator, 2, wintypes.ULONG)(enumerator)
        if policy_config:
            com_method(policy_config, 2, wintypes.ULONG)(policy_config)
        if initialized:
            ole32.CoUninitialize()

    return endpoint_name


def salida_predeterminada() -> str:
    """Nombre de la salida de audio predeterminada, p. ej. «Speakers (G533 Gaming Headset)»."""
    import comtypes
    from pycaw.pycaw import AudioUtilities

    comtypes.CoInitialize()
    try:
        return str(AudioUtilities.GetSpeakers().FriendlyName or "")
    finally:
        comtypes.CoUninitialize()


def es_auriculares(nombre: str) -> bool:
    nombre = nombre.lower()
    return "g533" in nombre or "headset" in nombre or "auricular" in nombre or "headphone" in nombre
