# MedLog schemas

This directory contains the language-neutral contracts used by MedLog:

- [openapi.yaml](openapi.yaml): event API and payload schemas.
- [semconv/medlog.yaml](semconv/medlog.yaml) and [manifest](semconv/manifest.yaml):
  the Weaver registry and its pinned OpenTelemetry dependency.
- [semantic_schemas/](semantic_schemas/README.md): four unmodified OpenTelemetry
  v1.40.0 GenAI JSON schemas, with the upstream Apache-2.0 [license](semantic_schemas/LICENSE).

Request validation loads local files and needs no network access. From the parent
repository root, validate the registry with Weaver 0.26.1:

```sh
weaver registry check -r schemas/semconv
```

Weaver resolves the pinned upstream registry over the network. When this directory
is used as a standalone repository, run `weaver registry check -r semconv` instead.

## Receiver integration

Keep this directory at `schemas/`, alongside `event-receiver/`. The receiver's path
module resolves contracts relative to its own location, not the working directory.
The Docker image and offline bundle preserve this layout. These files are not a
Python package and are not included in the receiver's wheel.

Schema changes must be coordinated with receiver mappings, regression tests and
interoperability documentation in the consuming repository. Relocation alone must
preserve schema contents, versions and identifiers.

## Future repository extraction

The directory is currently tracked in the parent repository. Conversion to a
submodule is a separate change:

1. Export the committed contents of this directory as the new repository's root,
   preserving layout and attribution. Verify schema checksums against the source.
2. Replace the parent's tracked directory with a submodule at the same `schemas/`
   path, pinned to the verified commit. Commit that replacement and `.gitmodules`
   together; application loading paths remain unchanged.
3. Update setup documentation and missing-schema errors to recommend
   `git submodule update --init --recursive`.
4. Validate a fresh recursive clone, a clear failure for an uninitialized checkout,
   and both Docker build contexts and offline distribution. Bundles must contain
   schema file contents without requiring Git or network access on the target host.
5. Review subsequent schema upgrades as submodule commit changes, alongside any
   required receiver changes.
