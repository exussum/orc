#!/bin/sh
# Installs "Meet in Chrome", a small macOS app that owns the orc-chrome:// URL
# scheme and opens the link in Google Chrome. orc sends a Mac browser to that
# scheme when the Google Meet link on a push notification is clicked, so the
# meeting opens in Chrome even when another browser is the default.
#
# The app is compiled here with the osacompile that ships with macOS, so it is
# never downloaded and Gatekeeper has nothing to quarantine.
set -eu

APP="$HOME/Applications/Meet in Chrome.app"
PLIST="$APP/Contents/Info.plist"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/LaunchServices.framework/Versions/A/Support/lsregister
SRC=$(mktemp -t meet-in-chrome).applescript

cat > "$SRC" <<'APPLESCRIPT'
@APPLESCRIPT@
APPLESCRIPT

mkdir -p "$HOME/Applications"
rm -rf "$APP"
osacompile -o "$APP" "$SRC"
rm -f "$SRC"
plutil -replace CFBundleIdentifier -string io.orc.meet-in-chrome "$PLIST"
plutil -insert CFBundleURLTypes -json '[{"CFBundleURLName":"orc-chrome","CFBundleURLSchemes":["orc-chrome"]}]' "$PLIST"
plutil -insert LSUIElement -bool true "$PLIST"
"$LSREGISTER" -f "$APP"
echo "Installed $APP"
