#!/usr/bin/env python3
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "apps" / "music" / "ansible" / "roles" / "music-backend" / "tasks" / "main.yml"
DEFAULTS = REPO_ROOT / "apps" / "music" / "ansible" / "roles" / "music-backend" / "defaults" / "main.yml"
AUDIO_DEFAULTS = (
    REPO_ROOT
    / "apps"
    / "music"
    / "ansible"
    / "roles"
    / "music-audio-endpoint"
    / "defaults"
    / "main.yml"
)
MUSICCTL = REPO_ROOT / "apps" / "music" / "bin" / "musicctl"
MPD_CONFIG = (
    REPO_ROOT
    / "apps"
    / "music"
    / "ansible"
    / "roles"
    / "music-backend"
    / "templates"
    / "mpd.conf.j2"
)
SNAPSERVER_CONFIG = (
    REPO_ROOT
    / "apps"
    / "music"
    / "ansible"
    / "roles"
    / "music-backend"
    / "templates"
    / "snapserver.conf.j2"
)
REMOVAL_TASKS = (
    REPO_ROOT
    / "apps"
    / "music"
    / "ansible"
    / "roles"
    / "music-backend-removal"
    / "tasks"
    / "main.yml"
)
TAILNET_REMOVAL = (
    REPO_ROOT / "apps" / "music" / "ansible" / "playbooks" / "35-tailnet-remove.yml"
)


class MusicBackendRoleTest(unittest.TestCase):
    def test_pcm_format_and_volume_defaults_match_smsl_endpoint(self):
        defaults = DEFAULTS.read_text(encoding="utf-8")
        audio_defaults = AUDIO_DEFAULTS.read_text(encoding="utf-8")
        musicctl = MUSICCTL.read_text(encoding="utf-8")
        snapserver_config = SNAPSERVER_CONFIG.read_text(encoding="utf-8")

        self.assertIn('music_pcm_format: "48000:24:2"', defaults)
        self.assertIn("music_mpd_software_volume: 50", defaults)
        self.assertIn("sampleformat={{ music_pcm_format }}", snapserver_config)
        self.assertIn('music_endpoint_soundcard: "hw:CARD=AUDIO,DEV=0"', audio_defaults)
        self.assertIn('"hw:CARD=AUDIO,DEV=0"', musicctl)

    def test_external_cue_sheets_are_not_indexed_as_virtual_tracks(self):
        mpd_config = MPD_CONFIG.read_text(encoding="utf-8")

        self.assertIn("playlist_plugin {", mpd_config)
        self.assertIn('name "cue"', mpd_config)
        self.assertIn('enabled "no"', mpd_config)
        self.assertIn('mixer_type "software"', mpd_config)
        self.assertIn('format "{{ music_pcm_format }}"', mpd_config)

    def test_backend_install_rescans_existing_database_after_config_refresh(self):
        tasks = TASKS.read_text(encoding="utf-8")

        self.assertIn("Force MPD to rescan the music library", tasks)
        self.assertIn("Set MPD software volume", tasks)
        self.assertIn('printf "rescan\\nclose\\n"', tasks)
        self.assertIn('printf "setvol {{ music_mpd_software_volume | int }}\\nclose\\n"', tasks)
        self.assertLess(
            tasks.index("Wait for music backend containers to settle"),
            tasks.index("Force MPD to rescan the music library"),
        )
        self.assertLess(
            tasks.index("Force MPD to rescan the music library"),
            tasks.index("Set MPD software volume"),
        )
        self.assertLess(
            tasks.index("Set MPD software volume"),
            tasks.index("Verify music backend pod is running"),
        )

    def test_removal_has_a_closed_preserve_by_default_scope(self):
        tasks = REMOVAL_TASKS.read_text(encoding="utf-8")
        tailnet = TAILNET_REMOVAL.read_text(encoding="utf-8")

        self.assertIn("music_data_inventory_after == music_data_inventory_before", tasks)
        self.assertIn("not (music_removal_wipe_data | bool)", tasks)
        self.assertIn("klokast-music-library", tasks)
        self.assertIn("klokast-music-playlists", tasks)
        self.assertIn("music_state_volumes", tasks)
        self.assertIn("Prove that reconstructable Music objects are absent", tasks)
        self.assertIn("Prove that the reconstructable Music backend image is absent", tasks)
        self.assertNotIn("podman system prune", tasks)
        self.assertNotIn("podman volume prune", tasks)
        self.assertIn("tailscale-stale-device", tailnet)
        self.assertIn("tailscale_device_fail_on_online_exact: true", tailnet)
        self.assertIn('(box + "-audio", "tag:streamer")', MUSICCTL.read_text(encoding="utf-8"))
        self.assertIn('["localhost,"]', MUSICCTL.read_text(encoding="utf-8"))

    def test_musicctl_auto_discovers_and_propagates_the_tailnet_dns_name(self):
        musicctl = MUSICCTL.read_text(encoding="utf-8")

        self.assertIn('MAGICDNS_SUFFIX_TOOL = REPO_ROOT / "ansible" / "bin" / "magicdns-suffix"', musicctl)
        self.assertIn('env["KLOKAST_MAGICDNS_SUFFIX"] = magicdns_suffix()', musicctl)
        self.assertNotIn('os.environ.get("KLOKAST_MAGICDNS_SUFFIX", "example.ts.net")', musicctl)


if __name__ == "__main__":
    unittest.main()
