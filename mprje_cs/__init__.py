# Bumped in lockstep with countrystore_je.py's BUILD_VERSION. The CLI
# asserts these two match before doing anything else - if they don't, the
# mprje_cs/ folder is a different age than countrystore_je.py (a partial
# extraction/overwrite), which the version banner alone can't catch since
# it only lives in the top-level script.
PACKAGE_VERSION = "2026-09-21.1"
