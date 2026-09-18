#!/usr/bin/env bash
set -euo pipefail
export ANDROID_AVD_HOME="$RUNNER_TEMP/etrn-avd"
export ANDROID_USER_HOME="$RUNNER_TEMP/etrn-android"
export ANDROID_EMULATOR_HOME="$ANDROID_USER_HOME"
export PATH="$ANDROID_HOME/platform-tools:$PATH"
mkdir -p "$ANDROID_AVD_HOME" "$ANDROID_USER_HOME" evidence
for var in ANDROID_AVD_HOME ANDROID_USER_HOME ANDROID_EMULATOR_HOME; do echo "$var=${!var}" >> "$GITHUB_ENV"; done
echo "$ANDROID_HOME/platform-tools" >> "$GITHUB_PATH"
yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses >/dev/null || true
"$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" "emulator" "system-images;android-30;google_apis;x86_64"
printf 'no\n' | "$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager" create avd -n etrn_lab -k "system-images;android-30;google_apis;x86_64" -p "$ANDROID_AVD_HOME/etrn_lab.avd" --force
"$ANDROID_HOME/cmdline-tools/latest/bin/avdmanager" list avd > evidence/avd-manager.txt
"$ANDROID_HOME/emulator/emulator" -list-avds > evidence/avd-emulator.txt
test -f "$ANDROID_AVD_HOME/etrn_lab.ini"
grep -qx etrn_lab evidence/avd-emulator.txt
sudo chmod a+rw /dev/kvm
ADB="$ANDROID_HOME/platform-tools/adb"
"$ADB" version > evidence/adb-version.txt
# The daemon must exist before the emulator connects to it. Use one exact SDK adb.
"$ADB" kill-server || true
"$ADB" start-server
"$ADB" devices -l > evidence/adb-before-emulator.txt
"$ANDROID_HOME/emulator/emulator" -avd etrn_lab -port 5554 -no-window -no-audio -no-boot-anim -no-snapshot -no-metrics -gpu swiftshader_indirect -accel on -memory 2048 > evidence/emulator.log 2>&1 &
EMULATOR_PID=$!
BOOTED=no
for attempt in $(seq 1 90); do
  if ! kill -0 "$EMULATOR_PID" 2>/dev/null; then cat evidence/emulator.log; exit 1; fi
  if timeout 5 "$ADB" -s emulator-5554 shell getprop sys.boot_completed 2>/dev/null | grep -q 1; then BOOTED=yes; break; fi
  if (( attempt % 10 == 0 )); then "$ADB" devices -l | tee -a evidence/adb-boot-progress.txt; fi
  if (( attempt == 30 )); then "$ADB" reconnect offline || true; fi
  sleep 2
done
if [ "$BOOTED" != yes ]; then cat evidence/emulator.log; "$ADB" devices -l; exit 1; fi
"$ADB" devices -l > evidence/adb-ready.txt
"$ADB" -s emulator-5554 shell getprop ro.build.version.release > evidence/android-version.txt
"$ADB" -s emulator-5554 shell input keyevent 82
"$ADB" -s emulator-5554 shell settings put global window_animation_scale 0
"$ADB" -s emulator-5554 shell settings put global transition_animation_scale 0
"$ADB" -s emulator-5554 shell settings put global animator_duration_scale 0
