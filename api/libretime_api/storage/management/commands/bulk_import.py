import logging
from pathlib import Path
from typing import List, Optional

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandParser
from libretime_shared.files import compute_md5

from ...models import File, TrackType

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_EXTENSIONS = [
    ".flac",
    ".m4a",
    ".mp3",
    ".ogg",
    ".opus",
    ".wav",
]


class Command(BaseCommand):
    help = "Bulk file upload."

    def add_arguments(self, parser: CommandParser):
        parser.add_argument(
            "--path",
            help="Path to the directory to scan.",
            required=True,
        )
        parser.add_argument(
            "--track-type",
            help="Track type for the new files.",
        )
        parser.add_argument(
            "--allowed-extensions",
            help="Allowed file extensions.",
            action="append",
            default=DEFAULT_ALLOWED_EXTENSIONS,
        )
        parser.add_argument(
            "--delete-after-upload",
            help="Delete file if upload succeeded.",
            action="store_true",
        )
        parser.add_argument(
            "--delete-if-exists",
            help="Delete file if it already exists.",
            action="store_true",
        )

    def handle(self, *args, **options):
        url = settings.CONFIG.general.public_url
        auth_key = settings.CONFIG.general.api_key

        delete_after_upload = options.get("delete_after_upload", False)
        delete_if_exists = options.get("delete_if_exists", False)

        path = options.get("path")
        track_type = options.get("track_type", None)
        allowed_extensions = options.get("allowed_extensions")

        importer = Importer(url, auth_key, delete_after_upload, delete_if_exists)
        importer.import_dir(Path(path).resolve(), track_type, allowed_extensions)


class Importer:
    def __init__(
        self,
        url: str,
        auth_key: str,
        delete_after_upload: bool = False,
        delete_if_exists: bool = False,
    ) -> None:
        self.url = url
        self.auth_key = auth_key

        self.delete_after_upload = delete_after_upload
        self.delete_if_exists = delete_if_exists

    def _check_file_md5(self, filepath: Path) -> bool:
        file_md5 = compute_md5(filepath)

        return File.objects.filter(md5=file_md5).exists()

    def _upload_file(self, filepath: Path, track_type: Optional[str]) -> None:
        try:
            resp = requests.post(
                f"{self.url}/rest/media",
                auth=(self.auth_key, ""),
                files=[
                    ("file", (filepath.name, filepath.open("rb"))),
                ],
                timeout=30,
                cookies={"tt_upload": track_type} if track_type is not None else {},
            )
            resp.raise_for_status()

        except requests.exceptions.HTTPError as exception:
            raise RuntimeError(f"could not upload {filepath}") from exception

    def _delete_file(self, filepath: Path) -> None:
        logger.info(f"deleting {filepath}")
        filepath.unlink()

    def _handle_file(self, filepath: Path, track_type: Optional[str]) -> None:
        logger.debug(f"handling file {filepath}")

        if not filepath.is_file():
            raise ValueError(f"provided path {filepath} is not a file")

        if self._check_file_md5(filepath):
            logger.info(f"found similar md5sum, ignoring {filepath}")
            if self.delete_if_exists:
                self._delete_file(filepath)
            return

        self._upload_file(filepath, track_type)

        if self.delete_after_upload:
            self._delete_file(filepath)

    def _walk_dir(
        self,
        path: Path,
        track_type: Optional[str],
        allowed_extensions: List[str],
    ) -> None:
        if not path.is_dir():
            raise ValueError(f"provided path {path} is not a directory")

        for sub_path in path.iterdir():
            if sub_path.is_dir():
                self._walk_dir(sub_path, track_type, allowed_extensions)
                continue

            if sub_path.suffix.lower() not in allowed_extensions:
                continue

            self._handle_file(sub_path.resolve(), track_type)

    def _check_track_type(self, track_type: str) -> bool:
        return TrackType.objects.filter(code=track_type).exists()

    def import_dir(
        self,
        path: Path,
        track_type: Optional[str],
        allowed_extensions: List[str],
    ) -> None:
        if track_type is not None and not self._check_track_type(track_type):
            raise ValueError(f"provided track type {track_type} does not exist")

        allowed_extensions = [
            (x if x.startswith(".") else "." + x) for x in allowed_extensions
        ]

        self._walk_dir(path, track_type, allowed_extensions)
