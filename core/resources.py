"""Пути к ресурсам приложения (иконки и прочие файлы).

В сборке PyInstaller ресурсы распаковываются во временную папку
`sys._MEIPASS`, а при запуске из исходников лежат рядом с проектом.
Относительный путь вида 'assets/icons/new_grid.svg' зависит от текущей
рабочей директории, а в собранном exe не находится вовсе — поэтому все
обращения к ресурсам идут через resource_path().
"""

import os
import sys


def project_root() -> str:
    """Папка с ресурсами (в сборке — распакованный bundle)."""
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return bundle_dir

    # core/resources.py -> корень проекта
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resource_path(*parts: str) -> str:
    """Абсолютный путь к файлу ресурса."""
    return os.path.join(project_root(), *parts)
