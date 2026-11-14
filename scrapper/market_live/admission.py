"""Per-delivery live grant checks, also applied to REST recovery."""
from pathlib import Path
from scrapper.market_refresh.planner import aware


def admit(config, now, stop_event):
    if (stop_event.is_set() or (Path(config['state_dir'])/'stop.request').exists()
        or config.get('owner_approved') is not True or not config.get('activation_ref')
        or not aware(config['active_from']) <= aware(now) < aware(config['active_until'])):
        raise PermissionError('live acquisition window closed')
