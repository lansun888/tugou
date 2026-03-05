import yaml

config_path = "bsc_bot/config.yaml"
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# Modify initial_stop_loss to 200 (meaning +200%)
# Current PnL is ~112%, so 112 <= 200 is True -> Trigger Stop Loss
if 'position_management' not in config:
    config['position_management'] = {}
if 'trailing_stop' not in config['position_management']:
    config['position_management']['trailing_stop'] = {}

config['position_management']['trailing_stop']['initial_stop_loss'] = 200

with open(config_path, 'w', encoding='utf-8') as f:
    yaml.dump(config, f, allow_unicode=True)

print("Modified initial_stop_loss to 200%.")
