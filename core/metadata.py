"""
metadata.py - Comprehensive metadata extraction and embedding engine.

Extracts container tags (title, artist, album, date/year, genre, track, comment)
and cover art from source video/media files.

Embeds tags into target audio formats using:
1. Native FFmpeg metadata arguments (-metadata key=value, -id3v2_version 3)
2. Professional Mutagen tagging post-processor for MP3 (ID3v2.3 APIC), FLAC,
   M4A (MP4 atoms & covr), OGG Vorbis, OPUS (METADATA_BLOCK_PICTURE), WAV, and AIFF.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from core.ffmpeg_finder import _get_creation_flags, find_ffmpeg, find_ffprobe
from core.probe import MediaProbeResult, probe_media


@dataclass
class MediaMetadata:
    """Standardized metadata representation for media files."""
    title: Optional[str] = None
    artist: Optional[str] = None
    album: Optional[str] = None
    album_artist: Optional[str] = None
    date: Optional[str] = None
    year: Optional[str] = None
    genre: Optional[str] = None
    track: Optional[str] = None
    disc: Optional[str] = None
    comment: Optional[str] = None
    composer: Optional[str] = None
    cover_bytes: Optional[bytes] = None
    cover_mime: Optional[str] = None
    extra_tags: dict[str, str] = field(default_factory=dict)

    def is_empty(self) -> bool:
        """Checks if all primary metadata fields and cover art are empty."""
        primary = [
            self.title,
            self.artist,
            self.album,
            self.album_artist,
            self.date,
            self.year,
            self.genre,
            self.track,
            self.disc,
            self.comment,
            self.composer,
            self.cover_bytes,
        ]
        return all(v is None for v in primary) and len(self.extra_tags) == 0


def detect_image_mime(image_data: bytes) -> str:
    """Detects MIME type from image header magic bytes."""
    if image_data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    elif image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    elif image_data.startswith(b"RIFF") and image_data[8:12] == b"WEBP":
        return "image/webp"
    elif image_data.startswith(b"GIF87a") or image_data.startswith(b"GIF89a"):
        return "image/gif"
    elif image_data.startswith(b"BM"):
        return "image/bmp"
    return "image/jpeg"


def extract_cover_art_bytes(
    file_path: str | Path,
    ffmpeg_path: Optional[str | Path] = None,
    probe_result: Optional[MediaProbeResult] = None,
) -> Optional[tuple[bytes, str]]:
    """
    Extracts embedded cover artwork from a media file.

    Returns:
        tuple[bytes, str]: (image_bytes, mime_type) or None if no cover art is found.
    """
    path_obj = Path(file_path).resolve()
    if not path_obj.is_file():
        return None

    probe = probe_result or probe_media(path_obj)
    if not probe.has_cover_art:
        return None

    ffmpeg_bin = find_ffmpeg(custom_path=ffmpeg_path)
    stream_idx = probe.cover_art_stream_index
    map_spec = f"0:{stream_idx}" if stream_idx is not None else "0:v"

    cmd = [
        str(ffmpeg_bin),
        "-v",
        "quiet",
        "-nostats",
        "-i",
        str(path_obj),
        "-map",
        map_spec,
        "-c:v",
        "copy",
        "-f",
        "image2pipe",
        "-",
    ]

    try:
        res = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_get_creation_flags(),
            check=True,
            timeout=20,
        )
        if res.stdout and len(res.stdout) > 0:
            mime = detect_image_mime(res.stdout)
            return res.stdout, mime
    except Exception:
        pass

    return None


def extract_metadata(
    file_path: str | Path,
    ffprobe_path: Optional[str | Path] = None,
    ffmpeg_path: Optional[str | Path] = None,
    extract_cover: bool = True,
) -> MediaMetadata:
    """
    Analyzes a media file and extracts normalized metadata tags and embedded cover artwork.

    Args:
        file_path: Path to media file.
        ffprobe_path: Optional path to ffprobe.
        ffmpeg_path: Optional path to ffmpeg.
        extract_cover: If True, attempts to extract cover artwork image bytes.

    Returns:
        MediaMetadata populated instance.
    """
    path_obj = Path(file_path).resolve()
    probe = probe_media(path_obj, ffprobe_path=ffprobe_path)

    # Gather container and audio stream tags
    tags: dict[str, str] = dict(probe.tags)
    if probe.primary_audio_stream:
        # Merge audio stream tags, preserving non-empty existing tags
        for k, v in probe.primary_audio_stream.tags.items():
            if k not in tags:
                tags[k] = v

    # Case-insensitive helper
    def get_tag(*keys: str) -> Optional[str]:
        lower_map = {k.lower(): v for k, v in tags.items()}
        for key in keys:
            k_low = key.lower()
            if k_low in lower_map and lower_map[k_low].strip():
                return lower_map[k_low].strip()
        return None

    title = get_tag("title", "song", "track_title")
    artist = get_tag("artist", "author", "performer", "singer")
    album = get_tag("album")
    album_artist = get_tag("album_artist", "albumartist")
    date_val = get_tag("date", "year", "creation_time")
    genre = get_tag("genre")
    track = get_tag("track", "tracknumber")
    disc = get_tag("disc", "discnumber")
    comment = get_tag("comment", "description")
    composer = get_tag("composer")

    # Parse clean year from date if present
    year = None
    if date_val:
        # E.g. "2024-05-12" or "2024"
        parts = date_val.split("-")
        if len(parts[0]) == 4 and parts[0].isdigit():
            year = parts[0]

    cover_bytes: Optional[bytes] = None
    cover_mime: Optional[str] = None
    if extract_cover and probe.has_cover_art:
        extracted = extract_cover_art_bytes(path_obj, ffmpeg_path=ffmpeg_path, probe_result=probe)
        if extracted:
            cover_bytes, cover_mime = extracted

    # Store remaining non-standard tags
    known_keys = {
        "title", "artist", "album", "album_artist", "albumartist", "date", "year",
        "genre", "track", "tracknumber", "disc", "discnumber", "comment", "description",
        "composer", "author", "performer", "creation_time",
    }
    extra_tags = {k: v for k, v in tags.items() if k.lower() not in known_keys}

    return MediaMetadata(
        title=title,
        artist=artist,
        album=album,
        album_artist=album_artist,
        date=date_val,
        year=year,
        genre=genre,
        track=track,
        disc=disc,
        comment=comment,
        composer=composer,
        cover_bytes=cover_bytes,
        cover_mime=cover_mime,
        extra_tags=extra_tags,
    )


def build_ffmpeg_metadata_args(
    metadata: MediaMetadata,
    target_format: str,
) -> list[str]:
    """
    Constructs FFmpeg CLI arguments to write metadata tags into the destination file.
    """
    args: list[str] = []

    if metadata.title:
        args.extend(["-metadata", f"title={metadata.title}"])
    if metadata.artist:
        args.extend(["-metadata", f"artist={metadata.artist}"])
    if metadata.album:
        args.extend(["-metadata", f"album={metadata.album}"])
    if metadata.album_artist:
        args.extend(["-metadata", f"album_artist={metadata.album_artist}"])
    if metadata.date or metadata.year:
        args.extend(["-metadata", f"date={metadata.date or metadata.year}"])
    if metadata.genre:
        args.extend(["-metadata", f"genre={metadata.genre}"])
    if metadata.track:
        args.extend(["-metadata", f"track={metadata.track}"])
    if metadata.disc:
        args.extend(["-metadata", f"disc={metadata.disc}"])
    if metadata.comment:
        args.extend(["-metadata", f"comment={metadata.comment}"])
    if metadata.composer:
        args.extend(["-metadata", f"composer={metadata.composer}"])

    for k, v in metadata.extra_tags.items():
        args.extend(["-metadata", f"{k}={v}"])

    # ID3v2.3 tag format for maximum MP3 player compatibility
    clean_fmt = target_format.lower().lstrip(".")
    if clean_fmt == "mp3":
        args.extend(["-id3v2_version", "3"])

    return args


def embed_metadata_with_mutagen(
    audio_path: str | Path,
    metadata: MediaMetadata,
) -> bool:
    """
    Embeds metadata tags and cover artwork into an existing audio file using Mutagen.

    Supports:
    - MP3: Full ID3v2.3 / ID3v2.4 with APIC attached picture frame
    - FLAC: Native Vorbis comments and Picture block
    - M4A: MP4 atoms (\\xa9nam, \\xa9ART, \\xa9alb, covr)
    - OGG / OPUS: Vorbis comments and base64 METADATA_BLOCK_PICTURE
    - WAV: RIFF ID3 chunk
    - AIFF: AIFF ID3 chunk

    Returns:
        bool: True if embedding succeeded, False otherwise.
    """
    path_obj = Path(audio_path).resolve()
    if not path_obj.is_file():
        return False

    if metadata.is_empty():
        return True

    ext = path_obj.suffix.lower()

    try:
        if ext == ".mp3":
            return _embed_mp3(path_obj, metadata)
        elif ext == ".flac":
            return _embed_flac(path_obj, metadata)
        elif ext in (".m4a", ".mp4", ".aac"):
            return _embed_mp4(path_obj, metadata)
        elif ext == ".ogg":
            return _embed_ogg(path_obj, metadata)
        elif ext == ".opus":
            return _embed_opus(path_obj, metadata)
        elif ext == ".wav":
            return _embed_wav(path_obj, metadata)
        elif ext in (".aiff", ".aif"):
            return _embed_aiff(path_obj, metadata)
    except Exception:
        return False

    return False


def _embed_mp3(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.id3 import (
        ID3,
        APIC,
        COMM,
        ID3NoHeaderError,
        TALB,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TPE2,
        TRCK,
        TPOS,
    )

    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        tags = ID3()

    if meta.title:
        tags.setall("TIT2", [TIT2(encoding=3, text=[meta.title])])
    if meta.artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=[meta.artist])])
    if meta.album:
        tags.setall("TALB", [TALB(encoding=3, text=[meta.album])])
    if meta.album_artist:
        tags.setall("TPE2", [TPE2(encoding=3, text=[meta.album_artist])])
    if meta.year or meta.date:
        tags.setall("TDRC", [TDRC(encoding=3, text=[meta.year or meta.date])])
    if meta.genre:
        tags.setall("TCON", [TCON(encoding=3, text=[meta.genre])])
    if meta.track:
        tags.setall("TRCK", [TRCK(encoding=3, text=[meta.track])])
    if meta.disc:
        tags.setall("TPOS", [TPOS(encoding=3, text=[meta.disc])])
    if meta.comment:
        tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=[meta.comment])])

    if meta.cover_bytes:
        mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,  # Front cover
                desc="Cover",
                data=meta.cover_bytes,
            )
        )

    tags.save(str(path), v2_version=3)
    return True


def _embed_flac(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(str(path))

    if meta.title:
        audio["title"] = meta.title
    if meta.artist:
        audio["artist"] = meta.artist
    if meta.album:
        audio["album"] = meta.album
    if meta.album_artist:
        audio["albumartist"] = meta.album_artist
    if meta.date or meta.year:
        audio["date"] = meta.date or meta.year
    if meta.genre:
        audio["genre"] = meta.genre
    if meta.track:
        audio["tracknumber"] = meta.track
    if meta.disc:
        audio["discnumber"] = meta.disc
    if meta.comment:
        audio["comment"] = meta.comment

    if meta.cover_bytes:
        audio.clear_pictures()
        pic = Picture()
        pic.type = 3  # Cover front
        pic.mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        pic.desc = "Front Cover"
        pic.data = meta.cover_bytes
        audio.add_picture(pic)

    audio.save()
    return True


def _embed_mp4(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.mp4 import MP4, MP4Cover

    try:
        audio = MP4(str(path))
    except Exception:
        return False

    if meta.title:
        audio["\xa9nam"] = [meta.title]
    if meta.artist:
        audio["\xa9ART"] = [meta.artist]
    if meta.album:
        audio["\xa9alb"] = [meta.album]
    if meta.album_artist:
        audio["aART"] = [meta.album_artist]
    if meta.date or meta.year:
        audio["\xa9day"] = [meta.date or meta.year]
    if meta.genre:
        audio["\xa9gen"] = [meta.genre]
    if meta.comment:
        audio["\xa9cmt"] = [meta.comment]

    if meta.track:
        try:
            trk_val = int(meta.track.split("/")[0])
            audio["trkn"] = [(trk_val, 0)]
        except Exception:
            pass

    if meta.disc:
        try:
            disc_val = int(meta.disc.split("/")[0])
            audio["disk"] = [(disc_val, 0)]
        except Exception:
            pass

    if meta.cover_bytes:
        mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        img_format = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        audio["covr"] = [MP4Cover(meta.cover_bytes, imageformat=img_format)]

    audio.save()
    return True


def _embed_ogg(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.flac import Picture
    from mutagen.oggvorbis import OggVorbis

    audio = OggVorbis(str(path))
    if meta.title:
        audio["title"] = meta.title
    if meta.artist:
        audio["artist"] = meta.artist
    if meta.album:
        audio["album"] = meta.album
    if meta.album_artist:
        audio["albumartist"] = meta.album_artist
    if meta.date or meta.year:
        audio["date"] = meta.date or meta.year
    if meta.genre:
        audio["genre"] = meta.genre
    if meta.track:
        audio["tracknumber"] = meta.track
    if meta.disc:
        audio["discnumber"] = meta.disc
    if meta.comment:
        audio["comment"] = meta.comment

    if meta.cover_bytes:
        pic = Picture()
        pic.type = 3
        pic.mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        pic.desc = "Front Cover"
        pic.data = meta.cover_bytes
        encoded_data = base64.b64encode(pic.write()).decode("ascii")
        audio["metadata_block_picture"] = [encoded_data]

    audio.save()
    return True


def _embed_opus(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.flac import Picture
    from mutagen.oggopus import OggOpus

    audio = OggOpus(str(path))
    if meta.title:
        audio["title"] = meta.title
    if meta.artist:
        audio["artist"] = meta.artist
    if meta.album:
        audio["album"] = meta.album
    if meta.album_artist:
        audio["albumartist"] = meta.album_artist
    if meta.date or meta.year:
        audio["date"] = meta.date or meta.year
    if meta.genre:
        audio["genre"] = meta.genre
    if meta.track:
        audio["tracknumber"] = meta.track
    if meta.disc:
        audio["discnumber"] = meta.disc
    if meta.comment:
        audio["comment"] = meta.comment

    if meta.cover_bytes:
        pic = Picture()
        pic.type = 3
        pic.mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        pic.desc = "Front Cover"
        pic.data = meta.cover_bytes
        encoded_data = base64.b64encode(pic.write()).decode("ascii")
        audio["metadata_block_picture"] = [encoded_data]

    audio.save()
    return True


def _embed_wav(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.wave import WAVE
    from mutagen.id3 import ID3, APIC, TALB, TCON, TDRC, TIT2, TPE1

    try:
        audio = WAVE(str(path))
    except Exception:
        return False

    tags = audio.tags
    if tags is None:
        audio.add_tags()
        tags = audio.tags

    if meta.title:
        tags.setall("TIT2", [TIT2(encoding=3, text=[meta.title])])
    if meta.artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=[meta.artist])])
    if meta.album:
        tags.setall("TALB", [TALB(encoding=3, text=[meta.album])])
    if meta.date or meta.year:
        tags.setall("TDRC", [TDRC(encoding=3, text=[meta.date or meta.year])])
    if meta.genre:
        tags.setall("TCON", [TCON(encoding=3, text=[meta.genre])])

    if meta.cover_bytes:
        mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=meta.cover_bytes,
            )
        )

    audio.save()
    return True


def _embed_aiff(path: Path, meta: MediaMetadata) -> bool:
    from mutagen.aiff import AIFF
    from mutagen.id3 import APIC, TALB, TCON, TDRC, TIT2, TPE1

    try:
        audio = AIFF(str(path))
    except Exception:
        return False

    tags = audio.tags
    if tags is None:
        audio.add_tags()
        tags = audio.tags

    if meta.title:
        tags.setall("TIT2", [TIT2(encoding=3, text=[meta.title])])
    if meta.artist:
        tags.setall("TPE1", [TPE1(encoding=3, text=[meta.artist])])
    if meta.album:
        tags.setall("TALB", [TALB(encoding=3, text=[meta.album])])
    if meta.date or meta.year:
        tags.setall("TDRC", [TDRC(encoding=3, text=[meta.date or meta.year])])
    if meta.genre:
        tags.setall("TCON", [TCON(encoding=3, text=[meta.genre])])

    if meta.cover_bytes:
        mime = meta.cover_mime or detect_image_mime(meta.cover_bytes)
        tags.delall("APIC")
        tags.add(
            APIC(
                encoding=3,
                mime=mime,
                type=3,
                desc="Cover",
                data=meta.cover_bytes,
            )
        )

    audio.save()
    return True


def apply_metadata(
    audio_path: str | Path,
    metadata: MediaMetadata,
    prefer_mutagen: bool = True,
) -> bool:
    """
    Public API to write metadata tags and embedded cover artwork to an audio file.
    """
    if prefer_mutagen:
        return embed_metadata_with_mutagen(audio_path, metadata)
    return False
