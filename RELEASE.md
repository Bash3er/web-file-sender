# Building a Windows release

The Windows executable must be built on Windows (or by the included Windows GitHub Actions workflow); this repository does not commit compiled binaries.

## Local build on Windows

1. Install Python 3.10+ and Inno Setup 6.
2. Open PowerShell in the repository root.
3. Create and activate a virtual environment, then install build requirements:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements-build.txt
   python -m unittest discover -s tests -v
   python -m PyInstaller --noconfirm --clean WebFileSender.spec
   ```

4. The portable application folder is `dist\WebFileSender`. Zip that folder to make the portable package.
5. Build the installer from the repository root:

   ```powershell
   iscc installer\WebFileSender.iss
   ```

   The installer is written to `release\WebFileSender-Setup.exe`.

## Publish on GitHub

1. Push the source changes to the repository.
2. Create and push a version tag, for example `git tag v1.0.0` followed by `git push origin v1.0.0`.
3. GitHub Actions runs tests, builds the Windows app, packages the portable ZIP and installer, then attaches both to a GitHub Release.
4. Do not publish a release if tests fail. Test the resulting installer and portable ZIP on a Windows account that does not have Python installed.

## Notes

- The app currently runs as a console application so startup errors remain visible. A future release can add a proper tray/window launcher if desired.
- The portable build stores relative configuration in its application directory. Keep that folder writable and back it up before replacing it with a newer release.
- Windows may show SmartScreen warnings for unsigned builds. Signing requires a code-signing certificate; do not tell users to ignore warnings without verifying the download source.
