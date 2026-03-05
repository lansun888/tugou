import yaml

config_path = "bsc_bot/config.yaml"
with open(config_path, 'r', encoding='utf-8') as f:
    config = yaml.safe_load(f)

# Revert initial_stop_loss to -50
config['position_management']['trailing_stop']['initial_stop_loss'] = -50

with open(config_path, 'w', encoding='utf-8') as f:
    yaml.dump(config, f, allow_unicode=True)

print("Reverted initial_stop_loss to -50%.")
