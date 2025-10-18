"""Main application UI for capture, queue processing, and settings."""

from __future__ import annotations

import os
import threading
from datetime import datetime
from importlib import import_module
from pathlib import Path
from typing import Optional

from urllib.parse import urlparse

from kivy.clock import Clock
from kivy.factory import Factory
from kivy.lang import Builder
from kivy.metrics import dp
from kivy.utils import platform
from kivy.properties import BooleanProperty, ObjectProperty, StringProperty
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.tabbedpanel import TabbedPanel, TabbedPanelItem
from kivy.uix.popup import Popup
from kivy.uix.button import Button

from . import db, expiry, notify, queue_worker
from .i18n import _
from .logger import log_event
from .utils import compute_sha256, is_network_available


KV = """
#:import dp kivy.metrics.dp
#:import sp kivy.metrics.sp
#:import tr app.i18n._

<JPLabel@Label>:
    text_size: self.width, None
    halign: 'left'
    valign: 'top'
    size_hint_y: None
    height: self.texture_size[1] + dp(6)
    shorten: False
    markup: False
    font_size: sp(16)
    font_name: 'Default'

<JPButton@Button>:
    size_hint_y: None
    height: dp(44)
    font_size: sp(16)
    font_name: 'Default'
    padding: dp(12), dp(12)

<MainRoot>:
    do_default_tab: False
    controller: None
    tab_width: dp(140)
    font_name: 'Default'

    ListTab:
        id: list_tab
        text: tr('tab_list')
        controller: root.controller
    RegisterTab:
        id: register_tab
        text: tr('tab_register')
        controller: root.controller
    SettingsTab:
        id: settings_tab
        text: tr('tab_settings')
        controller: root.controller

<ListTab>:
    controller: None

    BoxLayout:
        orientation: 'vertical'
        padding: dp(12)
        spacing: dp(12)

        JPLabel:
            text: tr('list_header')
            bold: True

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(12)
            JPButton:
                text: tr('btn_refresh')
                on_release: root.controller.refresh_entries()
            JPButton:
                text: tr('btn_check')
                on_release: root.controller.run_check()

        ScrollView:
            do_scroll_x: False
            bar_width: dp(6)
            GridLayout:
                id: list_grid
                cols: 1
                size_hint_y: None
                height: self.minimum_height
                padding: dp(8), dp(4)
                spacing: dp(8)

        JPLabel:
            id: info_label
            text: ''
            color: 0.5, 0.5, 0.5, 1

<RegisterTab>:
    controller: None
    status_text: ''
    demo_mode: False

    BoxLayout:
        orientation: 'vertical'
        padding: dp(12)
        spacing: dp(12)

        JPLabel:
            id: status_label
            text: root.status_text

        JPLabel:
            text: tr('label_demo_mode') if root.demo_mode else ''
            color: 1, 0, 0, 1
            opacity: 1 if root.demo_mode else 0
            size_hint_y: None
            height: dp(24) if root.demo_mode else 0

        JPButton:
            text: tr('btn_camera')
            on_release: root.controller.on_click_capture()

        JPLabel:
            text: tr('hint_placeholder')
            color: 0.5, 0.5, 0.5, 1

<SettingsTab>:
    controller: None
    status_text: ''

    BoxLayout:
        orientation: 'vertical'
        padding: dp(12)
        spacing: dp(12)

        JPLabel:
            text: tr('settings_url')

        TextInput:
            id: url_input
            multiline: False
            font_name: 'Default'
            hint_text: tr('settings_url_placeholder')
            text: root.controller.settings.base_url if root.controller else ''

        JPLabel:
            text: tr('settings_api_key')

        TextInput:
            id: key_input
            multiline: False
            password: True
            font_name: 'Default'
            text: root.controller.settings.api_key if root.controller else ''

        BoxLayout:
            size_hint_y: None
            height: dp(44)
            spacing: dp(12)
            JPButton:
                text: tr('btn_save')
                on_release: root.controller.save_settings(url_input.text, key_input.text)
            JPButton:
                text: tr('btn_test')
                on_release: root.controller.test_connection(url_input.text, key_input.text)

        JPLabel:
            id: status_label
            text: root.status_text
            color: 0.2, 0.4, 0.2, 1

"""


Builder.load_string(KV)


class MainRoot(TabbedPanel):
    controller = ObjectProperty(None)


class ListTab(TabbedPanelItem):
    controller = ObjectProperty(None)


class RegisterTab(TabbedPanelItem):
    controller = ObjectProperty(None)
    status_text = StringProperty("")
    demo_mode = BooleanProperty(False)


class SettingsTab(TabbedPanelItem):
    controller = ObjectProperty(None)
    status_text = StringProperty("")


class AppController:
    def __init__(self, app) -> None:
        self.app = app
        self.db_path = db.ensure_database(app.user_data_dir, seed_demo=False)
        self.settings = db.load_settings(self.db_path)
        self.root = MainRoot()
        self.root.controller = self
        ids = self.root.ids
        self.list_tab: ListTab = ids["list_tab"]
        self.register_tab: RegisterTab = ids["register_tab"]
        self.settings_tab: SettingsTab = ids["settings_tab"]
        self.register_tab.status_text = _("status_capture_ready")
        demo_flag = os.environ.get("FOODLAB_DEMO_MODE", "").strip().lower()
        self.register_tab.demo_mode = demo_flag in {"1", "true", "yes"}
        self.status_lock = threading.Lock()
        self.queue = queue_worker.QueueWorker(
            self.db_path,
            self.app.user_data_dir,
            self._load_settings,
            self._on_queue_status,
        )
        self._periodic = None
        self._capture_callback = None

    # ---- lifecycle -----------------------------------------------------

    def build(self):
        return self.root

    def on_start(self) -> None:
        self.refresh_entries()
        self.queue.start()
        self.queue.wake()
        self._periodic = Clock.schedule_interval(lambda _dt: self._tick(), 3600.0)

    def on_stop(self) -> None:
        if self._periodic is not None:
            self._periodic.cancel()
        self.queue.stop()

    def _tick(self) -> None:
        self.run_check(silent=True)

    # ---- settings ------------------------------------------------------

    def _load_settings(self) -> db.AppSettings:
        self.settings = db.load_settings(self.db_path)
        return self.settings

    def save_settings(self, url: str, api_key: str) -> None:
        try:
            base = (url or "").strip().rstrip('/') or self.settings.base_url
            key = (api_key or "").strip()
            settings = db.AppSettings(base_url=base, api_key=key)
            db.save_settings(self.db_path, settings)
            self.settings = settings
            self.settings_tab.status_text = _("settings_saved")
            self.queue.reconfigure()
        except Exception as exc:
            self.settings_tab.status_text = _("error_generic", exc)

    def test_connection(self, url: str, api_key: str) -> None:
        def _task():
            from .api_client import ApiClient
            base_url = (url or "").strip() or self.settings.base_url
            key = (api_key or "").strip()
            client = ApiClient(base_url, key, self.app.user_data_dir)
            try:
                result = client.health_check(with_api_key=bool(key))
                category = result.category
                if category == "ok":
                    message = _("settings_test_ok")
                elif category == "unauth":
                    message = _("settings_test_unauth")
                elif category == "dns":
                    message = _("settings_test_dns")
                elif category in {"timeout", "network"}:
                    detail = _(
                        "settings_test_detail",
                        result.host or (urlparse(base_url).hostname if base_url else "-"),
                        result.elapsed_ms,
                    )
                    message = f"{_('settings_test_network')}\n{detail}"
                elif category == "server":
                    message = _("error_server")
                else:
                    message = _("error_generic", result.detail or category)
            except Exception as exc:  # pragma: no cover - defensive
                message = _("error_generic", exc)

            def _done(_dt):
                self.settings_tab.status_text = message

            Clock.schedule_once(_done, 0)

        threading.Thread(target=_task, daemon=True).start()

    # ---- list ----------------------------------------------------------

    def refresh_entries(self) -> None:
        entries = db.fetch_entries(self.db_path)
        grid = self.list_tab.ids.list_grid
        grid.clear_widgets()
        if not entries:
            grid.add_widget(Factory.JPLabel(text=_("list_empty")))
        for entry in entries:
            grid.add_widget(self._make_entry_widget(entry))
        info = f"{_('db_path', self.db_path)}\n{_('img_dir', Path(self.app.user_data_dir) / 'photos')}\n{_('status_count', len(entries))}"
        self.list_tab.ids.info_label.text = info

    def _make_entry_widget(self, entry: db.FoodEntry):
        lines = []
        status_label = {
            "FRESH": _("fresh"),
            "DUE_SOON": _("due_soon"),
            "EXPIRED": _("expired"),
        }.get(entry.status, entry.status)
        name = entry.name or _("unknown_name")
        header = f"{status_label} {name}"
        if entry.class_id:
            header += f" / {_('field_class', entry.class_id)}"
        lines.append(header)
        if entry.deadline_ts:
            lines.append(_("field_expiry", _format_datetime(entry.deadline_ts)))
        if entry.hours_left is not None:
            hours = max(int(entry.hours_left), 0)
            lines.append(_("field_hours_left", hours))
        if entry.storage_label:
            lines.append(_("field_storage_label", entry.storage_label))
        elif entry.storage:
            lines.append(_("field_storage", entry.storage))
        if entry.note:
            lines.append(_("field_note", entry.note))
        if entry.ripeness:
            lines.append(_("field_ripeness", entry.ripeness))
        if entry.upload_id:
            lines.append(_("field_upload_id", entry.upload_id))
        if entry.item_id:
            lines.append(_("field_item_id", entry.item_id))
        if entry.last_error and entry.sync_state == "error":
            lines.append(_("field_error", entry.last_error))
        widget = Factory.JPLabel(text="\n".join(lines))
        widget.padding = (dp(4), dp(4))
        return widget

    # ---- capture -------------------------------------------------------

    def on_click_capture(self) -> None:
        offline = not is_network_available(self.settings.base_url)
        self.register_tab.status_text = (
            _("status_offline_capture") if offline else _("status_uploading")
        )

        camera_flow = import_module("app.camera_flow")

        def _callback(success: bool, path: Optional[Path], message: Optional[str]) -> None:
            if not success or not path:
                self._handle_capture_failure(message)
                return

            if not path.exists() or path.stat().st_size <= 0:
                self.register_tab.status_text = _("status_camera_no_image")
                return

            digest = compute_sha256(path)
            if not digest:
                self.register_tab.status_text = _("status_camera_no_image")
                return

            entry_name = path.stem
            image_sha1 = camera_flow.compute_sha1(path)
            deadline = expiry.default_deadline()
            db.insert_photo_entry(self.db_path, entry_name, path, image_sha1, deadline)
            log_event(
                self.app.user_data_dir,
                f"QUEUE_ENQUEUE | photo_path={path.name} | len={path.stat().st_size} | sha256={digest[:8]} | ok=1",
            )
            camera_flow._log("enqueue_done", path=path.name, size=path.stat().st_size)
            self.queue.wake()
            status_msg = _("status_saved_with_name", entry_name)
            if message and message != "permission_denied":
                status_msg = f"{status_msg}\n{message}"
            self.register_tab.status_text = status_msg
            self.refresh_entries()
            self._capture_callback = None

        self._capture_callback = _callback
        camera_flow.configure_capture(self.app.user_data_dir, _callback)
        Clock.schedule_once(lambda *_: camera_flow.launch_camera_first(), 0)

    # Backwards compatibility alias for callers expecting the old name.
    handle_capture = on_click_capture

    def _handle_capture_failure(self, message: Optional[str]) -> None:
        if message == "permission_denied":
            self.register_tab.status_text = _("status_camera_permission_denied")
            self._show_permission_dialog()
            return
        if message:
            self.register_tab.status_text = message
        else:
            self.register_tab.status_text = _("status_camera_no_image")
        self._capture_callback = None

    def _show_permission_dialog(self) -> None:
        content = BoxLayout(orientation="vertical", spacing=dp(12), padding=dp(12))
        content.add_widget(Factory.JPLabel(text=_("dialog_camera_permission_body")))

        button_row = BoxLayout(spacing=dp(12), size_hint_y=None, height=dp(44))
        popup = Popup(
            title=_("dialog_camera_permission_title"),
            content=content,
            size_hint=(0.85, None),
            height=dp(220),
        )

        def _open_settings(_instance):
            popup.dismiss()
            self._open_app_settings()

        def _open_gallery(_instance):
            popup.dismiss()
            self._invoke_gallery()

        def _cancel(_instance):
            popup.dismiss()

        settings_btn = Button(text=_("dialog_button_settings"))
        settings_btn.bind(on_release=_open_settings)
        gallery_btn = Button(text=_("dialog_button_gallery"))
        gallery_btn.bind(on_release=_open_gallery)
        cancel_btn = Button(text=_("dialog_button_cancel"))
        cancel_btn.bind(on_release=_cancel)

        button_row.add_widget(settings_btn)
        button_row.add_widget(gallery_btn)
        button_row.add_widget(cancel_btn)
        content.add_widget(button_row)
        popup.open()

    def _invoke_gallery(self) -> None:
        if not self._capture_callback:
            return
        camera_flow = import_module("app.camera_flow")
        camera_flow.pick_from_gallery(self.app.user_data_dir, self._capture_callback)

    def _open_app_settings(self) -> None:
        if platform != "android":
            return
        try:
            from android.runnable import run_on_ui_thread
            from jnius import autoclass
        except Exception:
            return

        PythonActivity = autoclass('org.kivy.android.PythonActivity')
        Settings = autoclass('android.provider.Settings')
        Intent = autoclass('android.content.Intent')
        Uri = autoclass('android.net.Uri')
        activity = PythonActivity.mActivity
        if activity is None:
            return
        uri = Uri.fromParts('package', activity.getPackageName(), None)
        intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS)
        intent.setData(uri)

        @run_on_ui_thread
        def _launch():
            try:
                activity.startActivity(intent)
            except Exception:
                pass

        _launch()

    # ---- expiry check --------------------------------------------------

    def run_check(self, silent: bool = False) -> None:
        entries = db.fetch_entries(self.db_path)
        result = expiry.evaluate(entries)
        db.apply_status_updates(self.db_path, result.updates)
        for note in result.notifications:
            notify.send(note.title, note.message)
        if not silent:
            self.register_tab.status_text = _(
                "status_check_result",
                len(result.updates),
                len(result.notifications),
            )
        self.refresh_entries()

    # ---- queue feedback ------------------------------------------------

    def _on_queue_status(self, entry_id: str, state: str, message: str) -> None:
        if state in {"done", "error"}:
            self.refresh_entries()
        self.register_tab.status_text = message


def _format_datetime(ts: int) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
