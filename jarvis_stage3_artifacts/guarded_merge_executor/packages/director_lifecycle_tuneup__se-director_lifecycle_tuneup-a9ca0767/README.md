# Guarded Merge Package: director_lifecycle_tuneup

- Proposal ID: `se-director_lifecycle_tuneup-a9ca0767`
- Title: `Director Lifecycle Tuneup`
- Created at: `2026-04-22T15:03:15Z`
- Plan file: `C:\Users\Daniil Lapin\Downloads\supervisor_v1_5_smart_telegram (1)\supervisor_v1_5_smart_telegram\jarvis_stage3_artifacts\self_evolution\proposed_patches\director_lifecycle_tuneup_plan.md`
- Requires manual merge: `True`

## Targets
- `app/jarvis_local_n8n_director_pro_core.py`

## Safety flow
1. Review the original plan file.
2. Run `backup_targets.ps1`.
3. Review `candidate_patch_notes.md`.
4. Create or refine a real patch block.
5. Run compile + health + smoke.
6. Only then consider live merge.
