"""Package the extension and only the device helper sources; no backend credentials."""

import json
from pathlib import Path
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent


def build():
    package = json.loads((HERE / "package.json").read_text())
    version = package["version"]
    manifest = f'''<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">
 <Metadata><Identity Language="en-US" Id="jiraya-workspace" Version="{version}" Publisher="jiraya-local"/>
 <DisplayName>Jiraya Workspace Checkpoint</DisplayName><Description xml:space="preserve">{escape(package["description"])}</Description>
 <Tags>git</Tags><Categories>Other</Categories><GalleryFlags>Public</GalleryFlags>
 <Properties><Property Id="Microsoft.VisualStudio.Code.Engine" Value="^1.85.0"/><Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="workspace"/><Property Id="Microsoft.VisualStudio.Code.ExecutesCode" Value="true"/></Properties>
 <License>extension/LICENSE.txt</License></Metadata>
 <Installation><InstallationTarget Id="Microsoft.VisualStudio.Code"/></Installation><Dependencies/>
 <Assets><Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/><Asset Type="Microsoft.VisualStudio.Services.Content.Details" Path="extension/readme.md" Addressable="true"/><Asset Type="Microsoft.VisualStudio.Services.Content.License" Path="extension/LICENSE.txt" Addressable="true"/></Assets>
</PackageManifest>'''
    content_types = """<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="js" ContentType="application/javascript"/><Default Extension="json" ContentType="application/json"/><Default Extension="md" ContentType="text/markdown"/><Default Extension="txt" ContentType="text/plain"/><Default Extension="py" ContentType="text/plain"/><Default Extension="vsixmanifest" ContentType="text/xml"/></Types>"""
    entries = {
        "extension.vsixmanifest": manifest.encode(),
        "[Content_Types].xml": content_types.encode(),
    }
    for source, target in (
        ("package.json", "package.json"),
        ("extension.js", "extension.js"),
        ("managed-companion.js", "managed-companion.js"),
        ("companion-requirements.txt", "companion-requirements.txt"),
        ("README.md", "readme.md"),
        ("LICENSE", "LICENSE.txt"),
    ):
        entries["extension/" + target] = (HERE / source).read_bytes()
    for namespace in ("app", "app/integrations", "app/integrations/github"):
        entries[f"extension/companion/{namespace}/__init__.py"] = b""
    for source in (
        "app/local_companion/__init__.py",
        "app/local_companion/__main__.py",
        "app/local_companion/server.py",
        "app/local_companion/git.py",
        "app/integrations/github/validation.py",
    ):
        entries["extension/companion/" + source] = (ROOT / source).read_bytes()
    output = HERE / f"jiraya-workspace-{version}.vsix"
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for name, content in sorted(entries.items()):
            info = ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            archive.writestr(info, content)
    print(output)
    return output


if __name__ == "__main__":
    build()
