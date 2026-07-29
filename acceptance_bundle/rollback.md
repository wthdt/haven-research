# Rollback Instructions

## Remove plugin
```bash
# Remove from enabled plugins config
sed -i '' '/haven-market-model/d' ~/.hermes/config.yaml

# Remove plugin files
rm -rf ~/.hermes/plugins/haven_market_model

# Remove skill
rm -rf ~/.hermes/skills/haven-market-risk

# Remove cron script
rm -f ~/.hermes/scripts/haven-close-update.py

# Remove data store
rm -rf ~/.hermes/data/haven-market-model
```

## Revert repo
```bash
cd /Users/wth/haven_research_v0_4/haven_research
git checkout main
```

## What was NOT modified
- Production workflows
- Paper/Live trading
- Broker connections
- outputs/latest
- Hermes core source code
- ai_investment_platform_v1
