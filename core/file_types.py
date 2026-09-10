# -*- coding: utf-8 -*-
"""Single source of truth for file extensions and file dialog filters.

Filters used to be hardcoded english strings repeated in several places
("Images (*.png *.jpg ...)"), so they were never translated and easy to
desynchronise.  Every dialog builds its filter here and the visible names
come from i18n.
"""

import os

import i18n

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
PNG_EXTENSIONS = (".png",)
JPEG_EXTENSIONS = (".jpg", ".jpeg")
PROJECT_EXTENSION = ".fcproj"
PROJECT_EXTENSIONS = (PROJECT_EXTENSION,)
JSON_EXTENSIONS = (".json",)

# Order matters: it is the order of the export dialog filters.
EXPORT_FILTERS = (
    ("filter_png", PNG_EXTENSIONS),
    ("filter_jpeg", JPEG_EXTENSIONS),
)
EXPORT_EXTENSIONS = PNG_EXTENSIONS + JPEG_EXTENSIONS


def patterns(extensions):
    """"*.png *.jpg" style mask for a list of extensions."""
    return " ".join("*" + ext for ext in extensions)


def _entry(name_key, extensions):
    return "%s (%s)" % (i18n.t(name_key), patterns(extensions))


def images_filter():
    return _entry("filter_images", IMAGE_EXTENSIONS)


def project_open_filter():
    return ";;".join(
        (
            _entry("filter_project", PROJECT_EXTENSIONS + JSON_EXTENSIONS),
            _entry("filter_json", JSON_EXTENSIONS),
        )
    )


def project_save_filter():
    # Both formats, exactly as before: .fcproj by default and plain .json,
    # so a project saved by an older build can still be overwritten.
    return ";;".join(
        (
            _entry("filter_project", PROJECT_EXTENSIONS),
            _entry("filter_json", JSON_EXTENSIONS),
        )
    )


def export_filter():
    return ";;".join(_entry(key, exts) for key, exts in EXPORT_FILTERS)


def extension_for_filter(selected_filter, default=".png"):
    """Extension for the filter the user picked in a save dialog.

    The whole rendered string is compared first, so it keeps working in
    every language; the pattern mask is a fallback for platform dialogs
    that return a slightly different string.  Never matches on the word
    "JPEG", which used to break as soon as the filter was translated.
    """
    if not selected_filter:
        return default
    for key, extensions in EXPORT_FILTERS:
        if selected_filter == _entry(key, extensions):
            return extensions[0]
    for key, extensions in EXPORT_FILTERS:
        for ext in extensions:
            if "*" + ext in selected_filter:
                return extensions[0]
    return default


def has_extension(path, extensions):
    if not path:
        return False
    return os.path.splitext(path)[1].lower() in tuple(extensions)


def is_image_path(path):
    return has_extension(path, IMAGE_EXTENSIONS)


def is_project_path(path):
    return has_extension(path, PROJECT_EXTENSIONS + JSON_EXTENSIONS)


def ensure_extension(path, extension, allowed=None):
    """Append `extension` unless the path already ends with an accepted one."""
    if not path:
        return path
    current = os.path.splitext(path)[1].lower()
    accepted = tuple(allowed) if allowed else (extension,)
    if current in accepted:
        return path
    return path + extension
