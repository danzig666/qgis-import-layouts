"""Build an installable QGIS plugin zip into dist/."""

import configparser
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PLUGIN = "import_layouts"
EXCLUDE = {"icon_full.png"}


def main():
    meta = configparser.ConfigParser()
    meta.read(os.path.join(ROOT, PLUGIN, "metadata.txt"), encoding="utf-8")
    version = meta["general"]["version"]

    os.makedirs(os.path.join(ROOT, "dist"), exist_ok=True)
    target = os.path.join(ROOT, "dist", "{}-{}.zip".format(PLUGIN, version))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for folder, dirs, files in os.walk(os.path.join(ROOT, PLUGIN)):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for name in sorted(files):
                if name in EXCLUDE or name.endswith(".pyc"):
                    continue
                path = os.path.join(folder, name)
                zf.write(path, os.path.relpath(path, ROOT).replace(os.sep, "/"))
        zf.write(os.path.join(ROOT, "LICENSE"), PLUGIN + "/LICENSE")
    print(target)


if __name__ == "__main__":
    main()
