"""Camera and gallery capture workflow with robust fallbacks."""

from __future__ import annotations

import os
import time
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional

from kivy.clock import Clock
from kivy.utils import platform
from PIL import Image

from jnius import autoclass, cast, JavaException  # type: ignore[attr-defined]

Intent = autoclass('android.content.Intent')
ClipData = autoclass('android.content.ClipData')
PackageManager = autoclass('android.content.pm.PackageManager')
MediaStore = autoclass('android.provider.MediaStore')
BitmapFactory = autoclass('android.graphics.BitmapFactory')
Bitmap = autoclass('android.graphics.Bitmap')
CompressFormat = autoclass('android.graphics.Bitmap$CompressFormat')
FileOutputStream = autoclass('java.io.FileOutputStream')
PythonActivity = autoclass('org.kivy.android.PythonActivity')
ContentValues = autoclass('android.content.ContentValues')
MediaColumns = autoclass('android.provider.MediaStore$MediaColumns')
ImagesMedia = autoclass('android.provider.MediaStore$Images$Media')
BuildVersion = autoclass('android.os.Build$VERSION')
File = autoclass('java.io.File')
FileProvider = None  # type: ignore

# ---- logging helper (anchor logs) ----
try:  # pragma: no cover - logger optional
    from kivy.logger import Logger

    def alog(tag: str, **kv):
        msg = " ".join(f"{k}={v}" for k, v in kv.items())
        Logger.info(f"CAMERA_FLOW {tag} {msg}")

except Exception:  # pragma: no cover - fallback when Logger unavailable

    def alog(tag: str, **kv):  # type: ignore[override]
        return

# --------------------------------------

from .i18n import _
from .logger import log_event

CaptureCallback = Callable[[bool, Optional[Path], Optional[str]], None]

DEMO_MODE = os.environ.get("FOODLAB_DEMO_MODE", "").strip().lower() in {"1", "true", "yes"}

_REQUEST_CODE_CAMERA = 0xCA71
_REQUEST_CODE_GALLERY = 0x11F8

_STATE: dict[str, object] = {}
_CONFIG: dict[str, object] = {
    "user_data_dir": None,
    "callback": None,
}


def configure_capture(user_data_dir: str | Path, callback: CaptureCallback) -> None:
    """Persist capture context for later camera or gallery launches."""

    _CONFIG["user_data_dir"] = str(user_data_dir)
    _CONFIG["callback"] = callback


def launch_camera_first() -> None:
    """Launch the camera flow using previously stored configuration."""

    user_dir_obj = _CONFIG.get("user_data_dir") or os.environ.get("FOODLAB_USER_DATA_DIR")
    callback = _CONFIG.get("callback")
    user_dir = str(user_dir_obj) if user_dir_obj else None
    if not user_dir or callback is None:
        raise RuntimeError("camera_flow not configured; call configure_capture() first")

    _log("launch_camera_first")
    capture_photo(user_dir, callback)


def capture_photo(user_data_dir: str | Path, callback: CaptureCallback) -> None:
    """Launch the platform camera, falling back to the gallery where needed."""

    _prepare_state(user_data_dir, callback)
    _log("capture_photo")
    if platform == "android":
        _ensure_permissions(_launch_camera, lambda: _finish(False, None, "permission_denied"))
    else:
        _launch_desktop_filechooser()


def pick_from_gallery(user_data_dir: str | Path, callback: CaptureCallback) -> None:
    """Directly launch the gallery file picker."""

    _prepare_state(user_data_dir, callback)
    _log("gallery_requested")
    if platform == "android":
        _ensure_permissions(lambda: _launch_gallery(fallback=False), lambda: _finish(False, None, "permission_denied"))
    else:
        _launch_desktop_filechooser()


def compute_sha1(path: Path) -> str:
    import hashlib

    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---- helpers -------------------------------------------------------------


def _prepare_state(user_data_dir: str | Path, callback: CaptureCallback) -> None:
    photos_dir = Path(user_data_dir) / "photos"
    photos_dir.mkdir(parents=True, exist_ok=True)

    _STATE.clear()
    _STATE.update(
        {
            "callback": callback,
            "photos_dir": photos_dir,
            "user_data_dir": str(user_data_dir),
            "fallback_used": False,
            "pending_path": None,
            "capture_uri": None,
            "capture_mode": None,
            "resolver": None,
            "capture_file": None,
        }
    )


def _ensure_permissions(on_granted: Callable[[], None], on_denied: Callable[[], None]) -> None:
    if platform != "android":
        on_granted()
        return

    try:
        from android.permissions import Permission, check_permission, request_permissions
    except Exception:
        on_granted()
        return

    wants = [Permission.CAMERA]
    try:
        wants.append(Permission.READ_MEDIA_IMAGES)
    except AttributeError:
        wants.append(Permission.READ_EXTERNAL_STORAGE)

    missing = [perm for perm in wants if not check_permission(perm)]
    if not missing:
        _log("ensure_permissions_ok")
        on_granted()
        return

    _log("ensure_permissions_request", missing=",".join(missing))

    def _callback(_permissions, grants):
        if all(grants):
            _log("ensure_permissions_ok")
            on_granted()
        else:
            _log("ensure_permissions_denied")
            on_denied()

    request_permissions(missing, _callback)


def _grant_uri_to_handlers(activity, intent, uri) -> None:
    """Grant temporary read/write permission for all activities handling the intent."""

    try:
        flags = Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_GRANT_WRITE_URI_PERMISSION
        try:
            intent.addFlags(flags)
        except Exception as exc:
            _log("grant_flags_fail", reason=str(exc))

        pm = activity.getPackageManager()
        handlers = pm.queryIntentActivities(intent, 0)
        count = 0
        if handlers is not None:
            size = handlers.size()
            for index in range(size):
                pkg = handlers.get(index).activityInfo.packageName
                try:
                    activity.grantUriPermission(pkg, uri, flags)
                    count += 1
                except Exception as exc:
                    _log("grant_perm_fail", pkg=str(pkg), reason=str(exc))
        _log("grant_uri_done", count=count, uri=str(uri))
    except Exception as exc:
        _log("grant_uri_fail", reason=str(exc))


def _get_fileprovider():
    global FileProvider
    if FileProvider is not None:
        return FileProvider

    try:
        FileProvider = autoclass('androidx.core.content.FileProvider')
        _log('fp_import_ok', flavor='androidx')
        return FileProvider
    except Exception as exc_androidx:
        try:
            FileProvider = autoclass('android.support.v4.content.FileProvider')
            _log('fp_import_ok', flavor='supportv4')
            return FileProvider
        except Exception as exc_support:
            _log('fp_import_fail', ax=str(exc_androidx), v4=str(exc_support))
            FileProvider = None
            return None


def _save_stream_to_file_via_bitmap(resolver, uri, out_path: str, quality: int = 95):
    """Decode the given URI into a bitmap and compress it to JPEG at out_path."""

    try:
        in_stream = resolver.openInputStream(uri)
        if in_stream is None:
            _log("open_stream_none", uri=str(uri))
            return False, "open_stream_none"

        bmp = BitmapFactory.decodeStream(in_stream)
        try:
            in_stream.close()
        except Exception:
            pass

        if bmp is None:
            _log("bitmap_decode_none", uri=str(uri))
            return False, "bitmap_decode_none"

        fos = FileOutputStream(out_path)
        ok = bmp.compress(CompressFormat.JPEG, quality, fos)
        try:
            fos.flush()
            fos.close()
        except Exception:
            pass

        if not ok:
            _log("bitmap_compress_fail", path=str(out_path))
            return False, "bitmap_compress_fail"

        _log("save_ok", path=str(out_path))
        return True, None

    except JavaException as jex:
        _log("save_java_exc", reason=str(jex))
        return False, str(jex)
    except Exception as exc:
        _log("save_py_exc", reason=str(exc))
        return False, str(exc)


def _launch_camera() -> None:
    _log("launch_begin", mode="camera")

    def _fallback(reason: str) -> None:
        _log("launch_fail", mode="camera", reason=reason)
        try:
            alog("fallback", reason=str(reason))
        except Exception:
            pass
        _cleanup_capture_uri(_STATE.get("resolver"), _STATE.get("capture_uri"), _STATE.get("capture_file"))
        _reset_capture_state()
        _launch_gallery(fallback=True, reason=reason)

    try:
        from android import activity
        from android.runnable import run_on_ui_thread
    except Exception as exc:  # pragma: no cover - platform specific
        _fallback(str(exc))
        return

    activity_instance = PythonActivity.mActivity
    if activity_instance is None:
        _fallback("no_activity")
        return

    photos_dir_obj = _STATE.get("photos_dir")
    if photos_dir_obj is None:
        _fallback("no_photos_dir")
        return

    photos_dir = Path(photos_dir_obj)  # type: ignore[arg-type]
    resolver = activity_instance.getContentResolver()
    sdk_int = int(BuildVersion.SDK_INT)

    timestamp = time.strftime("%Y%m%d-%H%M%S")
    display_name = f"IMG-{timestamp}.jpg"
    target_path = photos_dir / display_name
    if target_path.exists():
        target_path = _unique_filename(photos_dir)

    intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
    capture_uri = None
    capture_file_path: Optional[Path] = None

    try:
        if sdk_int >= 29:
            values = ContentValues()
            values.put(MediaColumns.DISPLAY_NAME, display_name)
            values.put(MediaColumns.MIME_TYPE, "image/jpeg")
            values.put(MediaColumns.RELATIVE_PATH, "Pictures/FoodLab")
            try:
                alog("ms_init", name=str(display_name), mime="image/jpeg", relpath="Pictures/FoodLab")
            except Exception:
                pass

            try:
                capture_uri = resolver.insert(ImagesMedia.EXTERNAL_CONTENT_URI, values)
                try:
                    alog("ms_insert", ok=str(capture_uri is not None), uri=str(capture_uri))
                except Exception:
                    pass
            except Exception as exc:
                try:
                    alog("ms_insert_fail", err=repr(exc))
                except Exception:
                    pass
                raise
            if capture_uri is None:
                raise RuntimeError("mediastore_insert_failed")

            intent.putExtra(MediaStore.EXTRA_OUTPUT, cast('android.os.Parcelable', capture_uri))

            try:
                clip = ClipData.newUri(activity_instance.getContentResolver(), "capture", capture_uri)
                intent.setClipData(clip)
                _log("clipdata_attach_ok")
            except Exception as exc:  # pragma: no cover - defensive log
                _log("clipdata_attach_fail", reason=str(exc))

            try:
                _grant_uri_to_handlers(activity_instance, intent, capture_uri)
            except Exception as exc:
                _log("prepare_capture_intent_fail", reason=str(exc))
                raise

            _STATE.update(
                {
                    "capture_mode": "mediastore",
                    "resolver": resolver,
                    "capture_uri": capture_uri,
                    "pending_path": target_path,
                    "capture_file": None,
                }
            )
        else:
            files_dir = activity_instance.getFilesDir()
            photo_dir = File(files_dir, "photos")
            if not photo_dir.exists():
                photo_dir.mkdirs()
            photo_file = File(photo_dir, display_name)
            provider = _get_fileprovider()
            if provider is None:
                _log("fileprovider_missing")
                _cleanup_capture_uri(resolver, None, None)
                _reset_capture_state()
                _launch_gallery(fallback=True, reason="fileprovider_missing")
                return

            provider_authority = f"{activity_instance.getPackageName()}.fileprovider"
            capture_uri = provider.getUriForFile(activity_instance, provider_authority, photo_file)

            intent.putExtra(MediaStore.EXTRA_OUTPUT, cast('android.os.Parcelable', capture_uri))
            try:
                clip = ClipData.newUri(activity_instance.getContentResolver(), "capture", capture_uri)
                intent.setClipData(clip)
                _log("clipdata_attach_ok")
            except Exception as exc:  # pragma: no cover - defensive log
                _log("clipdata_attach_fail", reason=str(exc))

            try:
                _grant_uri_to_handlers(activity_instance, intent, capture_uri)
            except Exception as exc:
                _log("prepare_capture_intent_fail", reason=str(exc))
                raise

            capture_file_path = Path(photo_file.getAbsolutePath())
            _STATE.update(
                {
                    "capture_mode": "fileprovider",
                    "resolver": resolver,
                    "capture_uri": capture_uri,
                    "pending_path": capture_file_path,
                    "capture_file": capture_file_path,
                }
            )

        try:
            flags_val = 0
            has_clip = False
            if intent is not None:
                try:
                    flags_val = intent.getFlags()
                except Exception:
                    pass
                try:
                    has_clip = bool(intent.getClipData())
                except Exception:
                    has_clip = False
            has_extra = capture_uri is not None
            alog(
                "intent_ready",
                flags=hex(flags_val) if isinstance(flags_val, int) else str(flags_val),
                hasClip=has_clip,
                hasExtraOutput=has_extra,
                uri=str(capture_uri) if has_extra else None,
            )
        except Exception as exc:
            alog("intent_ready_log_fail", err=repr(exc))

        match_default_only = getattr(PackageManager, "MATCH_DEFAULT_ONLY", 0) if PackageManager is not None else 0
        handler = None
        no_handler_reason: Optional[str] = None
        try:
            package_manager = activity_instance.getPackageManager()
            handler = package_manager.resolveActivity(intent, match_default_only)
        except Exception as exc:
            no_handler_reason = str(exc)

        if handler is None:
            _log("camera_no_handler", reason=no_handler_reason)
            _cleanup_capture_uri(resolver, capture_uri, capture_file_path or _STATE.get("capture_file"))
            _reset_capture_state()
            _finish(False, None, _("status_camera_no_image"))
            return

        if capture_uri is not None:
            _grant_uri_to_handlers(activity_instance, intent, capture_uri)

        _log(
            "launch_camera_ready",
            mode=_STATE.get("capture_mode"),
            uri=str(_STATE.get("capture_uri")) if _STATE.get("capture_uri") else None,
        )
    except Exception as exc:  # pragma: no cover - defensive
        _fallback(str(exc))
        return

    try:
        from android import activity
    except Exception as exc:  # pragma: no cover - platform specific
        _fallback(str(exc))
        return

    def _result_handler(request_code, result_code, intent_obj):
        if request_code not in (_REQUEST_CODE_CAMERA, _REQUEST_CODE_GALLERY):
            return False
        try:
            activity.unbind(on_activity_result=_result_handler)
        except Exception:
            pass

        def _process(_dt):
            _handle_activity_result(request_code, int(result_code), intent_obj)

        Clock.schedule_once(_process, 0)
        return True

    activity.bind(on_activity_result=_result_handler)

    @run_on_ui_thread  # type: ignore[misc]
    def _launch():
        try:
            try:
                alog("camera_starting")
            except Exception:
                pass
            activity_instance.startActivityForResult(intent, _REQUEST_CODE_CAMERA)
            try:
                alog("camera_started")
            except Exception:
                pass
            _log("camera_launch_ok", uri=str(capture_uri))
            _log("launch_ok", mode="camera")
        except Exception as launch_exc:  # pragma: no cover - platform specific
            try:
                alog("camera_start_fail", err=repr(launch_exc))
            except Exception:
                pass
            _log("camera_launch_fail", reason=str(launch_exc))
            _fallback(str(launch_exc))

    _launch()


def _launch_gallery(fallback: bool, reason: Optional[str] = None) -> None:
    if fallback and _STATE.get("fallback_used"):
        _log("launch_fail", mode="gallery", reason="fallback_already_used")
        _finish(False, None, _("status_gallery_launch_failed"))
        return

    _STATE["fallback_used"] = _STATE.get("fallback_used") or fallback
    _log("launch_begin", mode="gallery", fallback=fallback, reason=reason)
    if fallback:
        _log("launch_fail", mode="camera", reason=f"gallery_fallback:{reason}")
        try:
            alog("fallback", reason=str(reason))
        except Exception:
            pass

    try:
        from android import activity
        from android.runnable import run_on_ui_thread
    except Exception as exc:  # pragma: no cover - platform specific
        _cleanup_capture_uri(_STATE.get("resolver"), _STATE.get("capture_uri"), _STATE.get("capture_file"))
        _reset_capture_state()
        _finish(False, None, _("status_gallery_launch_failed"))
        return

    activity_instance = PythonActivity.mActivity
    if activity_instance is None:
        _cleanup_capture_uri(_STATE.get("resolver"), _STATE.get("capture_uri"), _STATE.get("capture_file"))
        _reset_capture_state()
        _finish(False, None, _("status_gallery_launch_failed"))
        return

    sdk_int = int(BuildVersion.SDK_INT)
    if sdk_int >= 33:
        intent = Intent(MediaStore.ACTION_PICK_IMAGES)
    else:
        intent = Intent(Intent.ACTION_PICK)
        intent.setType("image/*")

    try:
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    except Exception:
        pass

    resolver = activity_instance.getContentResolver()
    _STATE.update({"capture_mode": "gallery", "resolver": resolver, "capture_uri": None, "capture_file": None})

    def _result_handler(request_code, result_code, intent_obj):
        if request_code not in (_REQUEST_CODE_CAMERA, _REQUEST_CODE_GALLERY):
            return False
        try:
            activity.unbind(on_activity_result=_result_handler)
        except Exception:
            pass

        def _process(_dt):
            _handle_activity_result(request_code, int(result_code), intent_obj)

        Clock.schedule_once(_process, 0)
        return True

    activity.bind(on_activity_result=_result_handler)

    @run_on_ui_thread  # type: ignore[misc]
    def _launch():
        try:
            activity_instance.startActivityForResult(intent, _REQUEST_CODE_GALLERY)
            _log("launch_ok", mode="gallery")
        except Exception as exc:
            _cleanup_capture_uri(_STATE.get("resolver"), _STATE.get("capture_uri"), _STATE.get("capture_file"))
            _reset_capture_state()
            _finish(False, None, _("status_gallery_launch_failed"))

    _launch()


def _launch_desktop_filechooser() -> None:
    _log("launch_begin", mode="filechooser")
    try:
        from plyer import filechooser
    except Exception:
        _finish(False, None, _("status_gallery_launch_failed"))
        return

    def _selected(selection: list[str]):
        if selection:
            path = Path(selection[0])
            if path.exists() and path.stat().st_size > 0:
                target = _materialize_from_path(path)
                _finish(True, target, None)
            else:
                _finish(False, None, _("status_camera_no_image"))
            return
        if DEMO_MODE:
            demo = _create_demo_image()
            _finish(True, demo, _("status_placeholder_used"))
        else:
            _finish(False, None, _("status_camera_cancelled"))

    try:
        filechooser.open_file(on_selection=_selected)
    except Exception as exc:
        _finish(False, None, str(exc))


def _handle_activity_result(request_code: int, result_code: int, intent_obj) -> None:
    # —— 安全日志：不对 intent_obj 调用任何 Java 方法（intent 可能为 null）——
    try:
        alog(
            "on_result",
            req=int(request_code) if request_code is not None else -1,
            res=int(result_code) if result_code is not None else -1,
            hasData=bool(intent_obj),
            clipCount=-1,
        )
    except Exception:
        pass

    if request_code == _REQUEST_CODE_CAMERA:
        _process_camera_result(result_code)
    elif request_code == _REQUEST_CODE_GALLERY:
        _process_gallery_result(result_code, intent_obj)


    if request_code == _REQUEST_CODE_CAMERA:
        _process_camera_result(result_code)
    elif request_code == _REQUEST_CODE_GALLERY:
        _process_gallery_result(result_code, intent_obj)


def _process_camera_result(result_code: int) -> None:
    photos_dir_obj = _STATE.get("photos_dir")
    photos_dir = Path(photos_dir_obj) if photos_dir_obj else Path(".")
    pending_path = _STATE.get("pending_path")
    capture_uri = _STATE.get("capture_uri")
    resolver = _STATE.get("resolver")
    capture_file = _STATE.get("capture_file")

    try:
        ActivityClass = autoclass('android.app.Activity')
    except Exception:
        ActivityClass = None

    if ActivityClass is None or result_code != ActivityClass.RESULT_OK:
        _log("camera_cancelled", code=result_code)
        _cleanup_capture_uri(resolver, capture_uri, capture_file)
        _reset_capture_state()
        try:
            alog("fallback", reason="camera_cancelled")
        except Exception:
            pass
        _launch_gallery(fallback=True, reason="camera_cancelled")
        return

    try:
        if capture_uri is None:
            _log("camera_missing_uri")
            _cleanup_capture_uri(resolver, capture_uri, capture_file)
            _finish(False, None, _("status_camera_no_image"))
            _reset_capture_state()
            return

        target: Path = (
            pending_path if isinstance(pending_path, Path)
            else Path(pending_path) if pending_path
            else _unique_filename(photos_dir)
        )
        target.parent.mkdir(parents=True, exist_ok=True)

        resolver_obj = resolver
        if resolver_obj is None:
            activity_instance = PythonActivity.mActivity
            resolver_obj = activity_instance.getContentResolver() if activity_instance else None

        if resolver_obj is None:
            _log("camera_missing_resolver")
            _cleanup_capture_uri(resolver, capture_uri, capture_file)
            _finish(False, None, _("status_camera_no_image"))
            _reset_capture_state()
            return

        ok, err = _save_stream_to_file_via_bitmap(resolver_obj, capture_uri, str(target))
        if not ok:
            _log("camera_save_fail", reason=str(err), uri=str(capture_uri), path=str(target))
            _cleanup_capture_uri(resolver, capture_uri, capture_file)
            _finish(False, None, _("status_camera_no_image"))
            _reset_capture_state()
            return

        size = target.stat().st_size if target.exists() else 0
        _log("copy_to_app_files", path=target.name if target.exists() else str(target), size=size)
        _log("camera_copy_ok", path=str(target), size=size)
        _cleanup_capture_uri(resolver, capture_uri, capture_file)
        _reset_capture_state()
        _finish(True, target, None)

    except JavaException as jex:
        _log("result_java_exc", reason=str(jex))
        _cleanup_capture_uri(resolver, capture_uri, capture_file)
        _finish(False, None, _("status_camera_no_image"))
        _reset_capture_state()
    except Exception as exc:
        _log("result_py_exc", reason=str(exc))
        _cleanup_capture_uri(resolver, capture_uri, capture_file)
        _finish(False, None, _("status_camera_no_image"))
        _reset_capture_state()


def _process_gallery_result(result_code: int, intent_obj) -> None:
    resolver = _STATE.get("resolver")
    capture_uri = _STATE.get("capture_uri")
    capture_file = _STATE.get("capture_file")

    try:
        ActivityClass = autoclass('android.app.Activity')
    except Exception:
        ActivityClass = None

    if ActivityClass is None or result_code != ActivityClass.RESULT_OK:
        _log("gallery_cancelled", code=result_code)
        _reset_capture_state()
        _finish(False, None, _("status_camera_cancelled"))
        return

    photos_dir_obj = _STATE.get("photos_dir")
    photos_dir = Path(photos_dir_obj) if photos_dir_obj else Path(".")
    pending_path = _STATE.get("pending_path")

    try:
        uri = None
        data = intent_obj
        if data is not None:
            try:
                clip = data.getClipData()
                if clip is not None and clip.getItemCount() > 0:
                    uri = clip.getItemAt(0).getUri()
            except Exception:
                pass
            if uri is None:
                uri = data.getData()

        _log("gallery_uri", uri=str(uri))

        if uri is None:
            _finish(False, None, _("status_camera_no_image"))
            _reset_capture_state()
            return

        resolver_obj = resolver
        if resolver_obj is None:
            activity_instance = PythonActivity.mActivity
            resolver_obj = activity_instance.getContentResolver() if activity_instance else None

        if resolver_obj is None:
            _log("gallery_resolver_missing")
            _finish(False, None, _("status_camera_no_image"))
            _reset_capture_state()
            return

        target = (
            pending_path if isinstance(pending_path, Path)
            else Path(pending_path) if pending_path
            else _unique_filename(photos_dir)
        )
        target.parent.mkdir(parents=True, exist_ok=True)

        ok, err = _save_stream_to_file_via_bitmap(resolver_obj, uri, str(target))
        if not ok:
            _log("gallery_copy_fail", reason=str(err), uri=str(uri), path=str(target))
            _finish(False, None, _("status_camera_no_image"))
            _reset_capture_state()
            return

        size = target.stat().st_size if target.exists() else 0
        _log("gallery_copy_ok", path=str(target), size=size)
        _finish(True, target, None)
        _reset_capture_state()

    except JavaException as jex:
        _log("result_java_exc", reason=str(jex))
        _finish(False, None, _("status_camera_no_image"))
        _reset_capture_state()
    except Exception as exc:
        _log("result_py_exc", reason=str(exc))
        _finish(False, None, _("status_camera_no_image"))
        _reset_capture_state()


def _copy_from_uri(uri) -> Path:
    resolver = _STATE.get("resolver")
    if resolver is None:
        raise RuntimeError("resolver_missing")

    photos_dir: Path = Path(_STATE.get("photos_dir"))  # type: ignore[arg-type]
    target = _unique_filename(photos_dir)

    stream = resolver.openInputStream(uri)
    if stream is None:
        raise RuntimeError("stream_none")

    data = stream.read()
    stream.close()
    if not data:
        raise RuntimeError("empty_image")

    image = Image.open(BytesIO(data))
    image.save(target, format="JPEG", quality=90)
    return target


def _materialize_from_path(source: Path) -> Path:
    photos_dir: Path = Path(_STATE.get("photos_dir"))  # type: ignore[arg-type]
    target = _unique_filename(photos_dir)
    image = Image.open(source)
    image.save(target, format="JPEG", quality=90)
    return target


def _create_demo_image() -> Path:
    photos_dir: Path = Path(_STATE.get("photos_dir"))  # type: ignore[arg-type]
    target = _unique_filename(photos_dir)
    image = Image.new("RGB", (720, 480), color=(200, 200, 200))
    image.save(target, format="JPEG", quality=90)
    return target


def _cleanup_capture_uri(resolver, uri, file_path=None):
    # 删除已插入但未使用的 MediaStore 记录；删除空文件
    try:
        alog(
            "cleanup",
            hasResolver=bool(resolver is not None),
            hasUri=bool(uri is not None),
            hasFile=bool(file_path is not None),
        )
    except Exception:
        pass
    try:
        if resolver is not None and uri is not None:
            resolver.delete(uri, None, None)
    except Exception:
        pass
    if file_path:
        try:
            p = Path(file_path)
            if p.exists() and p.stat().st_size == 0:
                p.unlink()
        except Exception:
            pass


def _reset_capture_state() -> None:
    try:
        alog("reset_state")
    except Exception:
        pass
    for k in ("capture_mode", "resolver", "pending_path", "capture_uri", "capture_file"):
        _STATE[k] = None


def _finish(success: bool, path: Optional[Path], message: Optional[str]) -> None:
    callback = _STATE.get("callback")
    if success and path is not None:
        size = None
        try:
            size = path.stat().st_size
        except Exception:
            pass
        _log("enqueue_done", path=str(path), size=size)
    elif not success and message:
        _log("capture_failed", message=message)

    if callback:
        callback(success, path, message)

    _STATE.clear()


def _unique_filename(target_dir: Path) -> Path:
    base = time.strftime("%Y%m%d-%H%M%S")
    candidate = target_dir / f"IMG-{base}.jpg"
    suffix = 1
    while candidate.exists():
        candidate = target_dir / f"IMG-{base}-{suffix}.jpg"
        suffix += 1
    return candidate


def _log(event: str, **meta) -> None:
    user_dir = _STATE.get("user_data_dir")
    if not user_dir:
        return
    parts = [f"{key}={value}" for key, value in meta.items() if value is not None]
    suffix = f" {' '.join(parts)}" if parts else ""
    log_event(user_dir, f"CAMERA_FLOW | event={event}{suffix}")


def _show_toast(text: str) -> None:  # pragma: no cover - platform specific
    if platform != "android":
        return
    try:
        from android.runnable import run_on_ui_thread
        from jnius import autoclass
    except Exception:
        return

    PythonActivity = autoclass("org.kivy.android.PythonActivity")
    Toast = autoclass("android.widget.Toast")
    String = autoclass("java.lang.String")

    activity = PythonActivity.mActivity
    if activity is None:
        return

    @run_on_ui_thread  # type: ignore[misc]
    def _toast():
        toast = Toast.makeText(activity, String(text), Toast.LENGTH_SHORT)
        toast.show()

    _toast()

# --- [patch] request codes for routing ---
try:
    REQ_CAMERA
    REQ_GALLERY
except NameError:
    REQ_CAMERA  = 4500  # keep your existing value if already defined elsewhere
    REQ_GALLERY = 4600  # keep your existing value if already defined elsewhere

