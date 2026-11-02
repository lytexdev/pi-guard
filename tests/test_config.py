from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pi_guard.config import load_config
from pi_guard.config.files import CameraConfig, FileConfig, load_file_config
from pi_guard.config.settings import Settings
from pi_guard.core.modes import Mode

from .conftest import TOKEN


def test_bot_id_is_derived_from_token() -> None:
    s = Settings(telegram_bot_token=TOKEN, telegram_admin_user_ids=[5])
    assert s.telegram_bot_id == 123456789


def test_bot_id_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        Settings(telegram_bot_token=TOKEN, telegram_bot_id=42, telegram_admin_user_ids=[5])


def test_bot_id_used_as_admin_is_rejected() -> None:
    with pytest.raises(ValidationError, match="bot's own ID"):
        Settings(telegram_bot_token=TOKEN, telegram_admin_user_ids=[123456789])


def test_malformed_token_is_rejected() -> None:
    with pytest.raises(ValidationError, match="malformed"):
        Settings(telegram_bot_token="nope", telegram_admin_user_ids=[5])


def test_telegram_requires_admin() -> None:
    with pytest.raises(ValidationError, match="ADMIN_USER_IDS"):
        Settings(telegram_bot_token=TOKEN)


def test_env_lists_and_mode_parse(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env = tmp_path / ".env"
    env.write_text(
        f"PI_GUARD_TELEGRAM_BOT_TOKEN={TOKEN}\n"
        "PI_GUARD_TELEGRAM_ADMIN_USER_IDS=11, 22\n"
        "PI_GUARD_TELEGRAM_ALLOWED_CHAT_IDS=-100123\n"
        "PI_GUARD_DEFAULT_MODE=away\n"
        "PI_GUARD_LANGUAGE=de\n"
        f"PI_GUARD_CONFIG_FILE={tmp_path / 'missing.yaml'}\n"
    )
    config = load_config(env)
    s = config.settings
    assert s.telegram_admin_user_ids == [11, 22]
    assert s.telegram_allowed_chat_ids == [-100123]
    assert s.default_mode is Mode.AWAY
    assert s.language == "de"
    assert config.cameras == []


def test_secrets_are_hidden_in_repr() -> None:
    s = Settings(telegram_bot_token=TOKEN, telegram_admin_user_ids=[5])
    assert TOKEN not in repr(s)
    assert s.secrets == [TOKEN]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timezone", "Mars/Olympus"),
        ("signal_cli_address", "localhost"),
        ("recording_retention_days", 0),
        ("motion_sensitivity", 101),
        ("mqtt_topic_prefix", "home/#"),
    ],
)
def test_invalid_values(field: str, value: object) -> None:
    with pytest.raises(ValidationError):
        Settings(telegram_enabled=False, **{field: value})


def test_signal_requires_number_and_admin() -> None:
    with pytest.raises(ValidationError, match="PHONE_NUMBER"):
        Settings(telegram_enabled=False, signal_enabled=True)
    with pytest.raises(ValidationError, match="international"):
        Settings(telegram_enabled=False, signal_admin_numbers=["0151 123"])


def test_face_recognition_requires_person_detection() -> None:
    with pytest.raises(ValidationError, match="person detection"):
        Settings(telegram_enabled=False, face_recognition_enabled=True)


def test_yaml_cameras(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
cameras:
  - id: living_room
    name: Living Room
    device: /dev/v4l/by-id/cam-a
    resolution: 1920x1080
    motion:
      zones:
        - name: door
          points: [[0, 0], [0.5, 0], [0.5, 1]]
  - id: entrance
    name: Entrance
    device: /dev/v4l/by-id/cam-b
    enabled: false
presence:
  devices:
    - id: phone
      name: Phone
      mac_address: AA-BB-CC-DD-EE-FF
"""
    )
    config = load_file_config(path)
    assert [c.id for c in config.cameras] == ["living_room", "entrance"]
    assert config.cameras[0].size == (1920, 1080)
    assert config.presence.devices[0].mac_address == "aa:bb:cc:dd:ee:ff"
    assert config.presence.devices[0].randomized_mac


@pytest.mark.parametrize(
    "camera",
    [
        {"id": "Living Room", "name": "x", "device": "/dev/video0"},
        {"id": "a", "name": "x", "device": "/dev/video0", "resolution": "big"},
        {"id": "a", "name": "x", "device": "/dev/video0", "bitrate": "fast"},
        {"id": "a", "name": "x", "device": "/dev/video0", "unknown_key": 1},
    ],
)
def test_invalid_camera(camera: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        CameraConfig.model_validate(camera)


def test_duplicate_camera_ids_rejected() -> None:
    cam = {"id": "a", "name": "A", "device": "/dev/video0"}
    with pytest.raises(ValidationError, match="duplicate camera"):
        FileConfig.model_validate({"cameras": [cam, {**cam, "device": "/dev/video2"}]})


def test_zone_points_must_be_fractions() -> None:
    with pytest.raises(ValidationError, match="fractions"):
        FileConfig.model_validate(
            {
                "cameras": [
                    {
                        "id": "a",
                        "name": "A",
                        "device": "/dev/video0",
                        "motion": {
                            "zones": [{"name": "z", "points": [[0, 0], [640, 0], [0, 480]]}]
                        },
                    }
                ]
            }
        )


def test_starter_config_survives_awkward_device_names(tmp_path: Path) -> None:
    from pi_guard.audio.devices import AudioDevice
    from pi_guard.cameras.discovery import VideoDevice, VideoFormat
    from pi_guard.config.starter import starter_config

    camera = VideoDevice(Path("/dev/video0"),
                         Path("/dev/v4l/by-id/usb-HD_Web_Camera_HD_Web_Camera_Ucamera001-video-index0"),
                         "HD Web Camera: HD Web Camera",
                         (VideoFormat("mjpeg", True, ((1280, 720),)),))  # fmt: skip
    audio = [
        # What a Raspberry Pi with a USB speaker, a USB microphone and a webcam reports.
        AudioDevice(0, "vc4hdmi0", 0, "vc4-hdmi – MAI PCM", True, False),
        AudioDevice(3, "Device", 0, "USB-Audio - USB PnP Audio Device – USB Audio", True, False),
        AudioDevice(3, "Device", 0, "USB-Audio - USB PnP Audio Device – USB Audio", False, True),
        AudioDevice(4, "Camera", 0, "USB-Audio - HD Web Camera – USB Audio", False, True),
        AudioDevice(5, "SF558", 0, "USB-Audio - SF-558 – USB Audio", False, True),
    ]
    path = tmp_path / "config.yaml"
    path.write_text(starter_config([camera], audio))
    config = load_file_config(path)
    assert config.cameras[0].name == "HD Web Camera: HD Web Camera"
    assert config.audio.speaker == "plughw:CARD=Device,DEV=0"  # USB before HDMI
    # Not the speaker's dummy input, not the webcam: the separate microphone.
    assert config.audio.microphone == "plughw:CARD=SF558,DEV=0"


def test_broken_yaml_gives_a_readable_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("cameras:\n  - id: a\n    name: HD Web Camera: HD Web Camera\n")
    with pytest.raises(ValueError, match=r"line 3, column 24"):
        load_file_config(path)
