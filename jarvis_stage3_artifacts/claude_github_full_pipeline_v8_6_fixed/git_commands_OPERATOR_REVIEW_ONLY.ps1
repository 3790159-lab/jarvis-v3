# Review before running manually
git checkout -b jarvis/operator-dashboard-v8-6
git add app/main.py app/routers/operator_dashboard.py
git commit -m "Add Jarvis operator dashboard read-only API"
git push -u origin jarvis/operator-dashboard-v8-6
gh pr create --title "Add Jarvis Operator Dashboard API" --body-file "jarvis_stage3_artifacts\claude_github_full_pipeline_v8_6_fixed\claude_code_package\github_pr_plan.md" --base main --head jarvis/operator-dashboard-v8-6
