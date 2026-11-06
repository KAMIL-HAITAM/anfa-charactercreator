# ANFA CharacterCreator — Claude Handoff

Updated: 2026-11-06

## Project purpose
This repository is a local working copy of `https://github.com/kblood/CharacterCreator`, being adapted for ANFA ATHLETICS' Outfit Lab. It uses Blender 5.2.3 LTS, MPFB 2 / MakeHuman assets, GLB garment models, and a local Three.js viewer.

## Critical state and user expectations
- The original upstream remote was `https://github.com/kblood/CharacterCreator.git`; preserve it under the name `upstream` when creating a separate private ANFA repo. Do not push ANFA work to the upstream project.
- The user wants this work moved into a separate private repo named `KAMIL-HAITAM/anfa-charactercreator` so Claude can see the current scripts and prototypes.
- The existing 3D work does NOT yet match the ANFA garment designs. Be explicit about that.
- User likes clear PowerShell commands and does not manually rename downloaded files; commands should copy versioned files to final project names.
- Inspect actual files and working tree before editing. Back up generated meshes/manifests before replacing them. Do not keep blindly generating alternate scripts without inspecting geometry / rendered results.

## Paths and runtime
Local working directory (Windows):
`C:\Users\Asus ExpertBook 15\Documents\anfa-charactercreator`

Blender executable used:
`C:\Program Files\Blender Foundation\Blender 5.2\blender.exe`

Standalone viewer staging folder: `build/site`, served at `http://localhost:8000/`. Keep distinct from ANFA Next.js website at `http://localhost:3000/`.

Relevant code/docs:
- `blender/build_base.py` — generates base human, native clothing, hair and output GLBs.
- `blender/cc_clothing.py` — garment definitions and fit/build pipeline.
- `web/main.js`, `web/clothing.js`, `web/character.js` — standalone viewer and clothing binding/colour logic.
- `tools/make_colliders.mjs` — collider sidecar generator.
- `docs/BLENDER_WORKFLOW.md` — documented native generation workflow.
- `output/clothing.json` — garment manifest.

## Current garment prototype results
Latest completed local native rebuild generated `output/clothing_tshirt.glb` (this file did not exist in an earlier state) and refreshed native outputs. A converter then generated:
- `output/clothing_anfa_core_form_tee.glb`
- `output/clothing_anfa_architectural_rib_leggings.glb`
- corresponding entries in `output/clothing.json` with IDs `anfa_core_form_tee` and `anfa_architectural_rib_leggings`.

The converter printed `GLB audit PASS, skins=1, skinned=True, targets=92` for both. This verifies basic GLB skin/morph structure, NOT design fidelity or correct shape.

A subsequent detail script ran, but the latest user screenshot showed a black oversized crew-neck T-shirt and plain straight black trousers. The shirt had a small low-contrast ANFA chest mark in the wrong position/scale; it still did not match the intended Core Form design. Architectural Rib external panels/ribbing were not visible enough. The user explicitly said it did not look like their T-shirt. Do not mark this as complete.

Local scripts from the experimentation are in `blender/tools/`:
- `build_anfa_garments.py`
- `build_anfa_from_originals.py`
- `build_anfa_from_native_tshirt.py`
- `build_anfa_finish_details.py`

Treat these as experiments. Inspect the actual code before running it. Past approach of clipping / extracting garment surfaces from the body's skin mesh resulted in jagged boundaries or shell-like clothing and was rejected. Do not revive that as the final approach.

## Correct design intent
Use the user's real ANFA design references, not generic stock clothes:
- **Core Form oversized T-shirt**: crew neck; boxy/oversized body; dropped shoulders; intentional short sleeves with rounded/finished edges; clean hem; matte washed-black colourway; small, restrained tonal ANFA chest mark placed on the upper chest, not mid-chest.
- **Architectural Rib leggings**: high-rise waistband; fitted smooth-knit leggings with separate/visible raised ribbed outer-leg panels; full ankle length; understated tonal ANFA branding. Do not use denim-like texture or call plain jeans the final model.

Images known from prior project work / ChatGPT file library, ensure they're included or made available if accessible:
- `ANFA Forged 01 T-Shirt Catalog.png`
- `ANFA Athletics Heavyweight Jogger Lookbook.png`
- `ANFA Athletics Seamless Leggings Lookbook.png`
- `ANFA Athletics Activewear Asset Catalog.png`
- `ANFA Technical Sports Bra Collection.png`
- `ANFA Outfit Lab Storyboard(1).png`
- `ANFA Outfit Lab: Complete UI Journey.png`
- `ANFA Athletics Forged 01 Lookbook.png`

## Working tree snapshot provided by user (before private-repo setup)
- Branch: `master`, tracking `origin/master`.
- `origin` still points to `https://github.com/kblood/CharacterCreator.git`; do not push there.
- Many tracked files in `output/` were modified by native rebuild, including body, clothing catalogue, clothing GLBs and hair GLBs.
- New prototype generator scripts and ANFA GLBs/textures were untracked.
- `mpfb.zip` is an untracked 43.83 MB installer and should not be committed.
- `build/` is ignored, including generated `build/mh_assets` (about 545.82 MB) and Blender scratch; continue to exclude it.
- Prototype backup files like `*.pre_original_mesh_build.glb`, `*.pre_finish_details.glb`, `*.pre_original_mesh_build.json` should be excluded from normal commits or stored outside tracked output. Preserve existing local copies.
- `output/anfa_core_form_tee_basecolor.png` and `output/anfa_architectural_rib_leggings_basecolor.png` are untracked and may be useful source textures; inspect references before deciding whether to commit.
- Current `.gitignore` did not explicitly ignore `.env` files or `mpfb.zip`. Add conservative secret/binary installer patterns before pushing. Scan for secrets and tracked `.env` paths first.

## Safe GitHub repo setup
Target repository: `https://github.com/KAMIL-HAITAM/anfa-charactercreator` (create it PRIVATE and EMPTY). No repository-create API action was available to the prior assistant, so the user may need to create it on GitHub UI or via `gh repo create` locally.

Before pushing:
1. Check `git status`, `git diff --stat`, branch and remotes.
2. Inspect git-tracked paths for `.env`, private credentials, keys, `mpfb.zip`, and giant binaries. The existing `build/` directory is ignored.
3. Extend `.gitignore` to ignore `.env`, `.env.*` (while allowing `.env.example`), certificates/private keys, `mpfb.zip`, and prototype `output/*.pre*.glb` / `output/*.pre*.json` backups.
4. Review whether outputs need tracking for the standalone viewer; include the output GLBs/manifests and source scripts needed to reproduce the preview, not the 545 MB `build/mh_assets` folder or `build/` transient.
5. Rename existing remote `origin` to `upstream` to preserve it, then add new private URL as `origin`. Do not push to `kblood/CharacterCreator`.
6. Commit reviewed changes and push `master` to new origin. Use normal push (no force) and preserve full upstream history unless there's a deliberate reason not to.
7. Verify on GitHub that the new repository is private and contains the handoff, current Blender scripts, GLBs and catalogue.

## ANFA website and commerce cross references
- Website repo: `https://github.com/KAMIL-HAITAM/anfa-athletics-website`; default branch checked was `master`. It has the working 2D Outfit Lab builder. Checked default branch did NOT contain `components/OutfitLab3DView.tsx` or a CharacterCreator reference, so newer local frontend 3D integration may not be pushed.
- Backend repo: `https://github.com/KAMIL-HAITAM/anfa-commerce-backend`; default branch checked was `main`.
- Website handoff: https://github.com/KAMIL-HAITAM/anfa-athletics-website/blob/master/CLAUDE_HANDOFF.md
- Backend handoff: https://github.com/KAMIL-HAITAM/anfa-commerce-backend/blob/main/CLAUDE_HANDOFF.md

## Suggested work sequence after repo is available
1. Verify repository contents/branch/working tree before changes.
2. Inspect actual Blender conversion scripts, native mesh, and output manifest.
3. Make a visual QA workflow with front/side/back screenshots and compare to real ANFA references.
4. Fix one garment at a time using actual garment geometry/topology, not a body-surface shell. Preserve and validate skinning, shape keys and hidden-skin zones.
5. Do not integrate into ANFA website until user accepts visual fit. Preserve existing 2D builder/cart.
