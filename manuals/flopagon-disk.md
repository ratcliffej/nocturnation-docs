# Flopagon disk — offline app distribution for Tildagon

The Flopagon is a Tildagon hexpansion PCB shaped like a 3.5" floppy
disk. When you plug it in, the badge auto-launches a **disk manager**
app that can install apps onto the badge, back apps up onto the disk,
or delete apps from either side — all without WiFi.

Built for the case where site WiFi is too poor to reach the Tildagon
app store (EMF Stage D at busy times, most festival crowds). Works for
any Tildagon app, not just NocturNation.

## Contents

1. [What you need](#what-you-need)
2. [Using a Flopagon](#using-a-flopagon)
3. [Preparing a fresh Flopagon](#preparing-a-fresh-flopagon)
4. [Troubleshooting](#troubleshooting)
5. [Disk layout reference](#disk-layout-reference)

## What you need

- A Flopagon PCB (Nathan Dumont's product, V1 with 2 KB EEPROM or V2
  with 8 KB). Both work; the black PCB is V2.
- A Tildagon badge running current firmware.
- One-off, per Flopagon: our bootstrap `.mpy` written to its EEPROM and
  the disk manager + your apps written to its 16 MB flash. See
  [Preparing a fresh Flopagon](#preparing-a-fresh-flopagon).

Nathing else. Day-to-day the badge and Flopagon are entirely offline.

## Using a Flopagon

1. **Insert the Flopagon** into any of the badge's six hexpansion
   ports. The badge auto-launches the disk manager.
2. **Pick an operation** from the hub menu:

   | Menu | What it does |
   |---|---|
   | `Install app` | Copy an app from the disk to the badge |
   | `Backup app` | Copy an app from the badge to the disk |
   | `Delete from disk` | Remove an app from the disk |
   | `Delete from badge` | Remove an app from the badge |
   | `Exit` | Return to the launcher |

3. **Pick the app.** A sub-menu shows the apps available for the
   operation you chose:
   - Install → apps on the disk
   - Backup / Delete from badge → apps on the badge (hidden system
     apps filtered out)
   - Delete from disk → apps on the disk
4. **Confirm.** A prompt shows the app's slug, name, and version.
   Press A to proceed, B to cancel.
5. **Wait.** Copies show a progress bar (~2.5 s for a 50-file app);
   deletes complete in one tick. Removing the Flopagon mid-copy aborts
   the current job with a visible error but leaves everything else
   untouched.
6. **Done.** Press any button to return to the hub menu, or Exit.

Newly-installed apps appear in the launcher immediately (no reboot);
newly-deleted apps disappear immediately.

## Preparing a fresh Flopagon

One-off setup per Flopagon PCB. After this the disk is ready for
day-to-day use.

**Prerequisites** on the host machine:

- `mpremote` and `mpy-cross` installed (`pip install mpremote mpy-cross`).
- The [nocturnation-disk][repo] source repo checked out.
- The Flopagon's write-protect jumper shorted (0.1" header near the
  edge — bridge with a jumper wire or tweezers). Development Flopagons
  are typically left permanently shorted.

**Steps:**

1. Insert the Flopagon into any port on the badge.
2. From the source repo, run the one-shot provisioning script:

   ```
   ./disk/dev/provision_flopagon.sh
   ```

   This compiles the bootstrap, formats the EEPROM, writes the
   bootstrap `.mpy`, populates the 16 MB flash with the disk manager
   + a sample app, then resets the badge.

3. Physically remove and re-insert the Flopagon. The hub menu should
   appear.
4. Remove the write-protect jumper (unless you want to keep the disk
   overwritable).

To add your own apps to a provisioned Flopagon, use the disk manager's
**Backup app** flow from a badge that already has the app installed.

The provisioning script assumes port 1 by default; edit `PORT` at the
top of `disk/dev/provision_flopagon.py` for other slots.

[repo]: https://github.com/ratcliffej/nocturnation-disk

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Badge shows `Disk error / Flash init failed` on insert | SPI flash didn't init | Remove + re-insert; the bootstrap retries once. If persistent, try a different port. |
| Badge shows `Disk error / No installer` | Bootstrap ran but the flash has no `/installer/` | Re-run `provision_flopagon.sh`. |
| Badge shows `Disk error / Mount` | LFS2 on the 16 MB flash is corrupt (rare) | Re-run `provision_flopagon.sh`; the populate step wipes + rewrites the flash. |
| Bootstrap error persists after remove + re-insert | Stuck EEPROM mount | Press F on the error screen to escape back to the launcher, then re-insert. |
| Hub menu appears but no apps show under Install | Flash has `/installer/` but no `/apps/*/` | Copy at least one app onto the disk (via Backup from a badge that has it, or via `mpremote cp`). |
| Copy fails mid-job with a visible error | Flopagon removed mid-op, or disk full | Re-insert, re-run the operation. Disk-full only realistic if you backed up multiple apps to a V1 (16 MB is plenty for tens of typical badge apps). |
| EEPROM appears fully corrupted (ENOSPC / ENAMETOOLONG loops during provisioning) | LFS2 metadata unrecoverable | Run `disk/dev/eeprom_reformat.py` via `mpremote` — nuclear reformat of the 2 KB EEPROM only. |

## Disk layout reference

The 16 MB flash is a LittleFS2 filesystem mounted at `/disk` on the
badge. Layout:

```
/disk/
├── installer/          # disk manager app (loaded by the bootstrap)
│   ├── app.py
│   ├── _jobs.py
│   ├── _badge_apps.py
│   ├── _fsutil.py
│   ├── _manifest.py
│   └── __init__.py
└── apps/
    ├── nocturnation/
    │   ├── disk.json   # manifest written by Backup
    │   ├── app.py
    │   ├── metadata.json
    │   └── ...
    └── <other-app>/
        └── ...
```

The 2 KB EEPROM (mounted transiently at `/hexpansion_<N>` on insert)
holds only the hexpansion identity header and the bootstrap `.mpy`.
No user-serviceable content there.

`disk.json` schema:

```json
{
    "manifest_version": 1,
    "name": "NocturNation",
    "slug": "nocturnation",
    "version": "1.0.18",
    "files": 51,
    "copied_at": 12345678
}
```

Backup writes it; Install reads it to show the display name in the
picker. Missing manifests fall back to the folder name.

## Related

- [nocturnation-disk][repo] — the disk manager source (installer,
  bootstrap, dev tooling, tests). Extracted from the NocturNation
  Tildagon firmware repo on 2026-07-25 so it can be developed
  independently of the audience-lighting app.
- `README.md` in the disk repo — module reference for anyone editing
  the disk manager.
- `bootstrap/README.md` in the disk repo — the 2 KB EEPROM bootstrap,
  including the byte-budget rationale.
- [Nathan Dumont's Flopagon][flopagon-hw] — the underlying hexpansion
  hardware (KiCad sources, manufacturing files, and Nathan's reference
  `app.mpy` mount helper).

[flopagon-hw]: https://github.com/hairymnstr/Flopagon
