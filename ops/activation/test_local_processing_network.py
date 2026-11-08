import pytest
from local_processing_network import assert_isolated


def test_dormant_tunnels_and_loopback_are_isolated():
    result=assert_isolated([(1,'lo'),(2,'tunl0'),(3,'gre0')],
        flags=lambda name:{'lo':0x49,'tunl0':0x80,'gre0':0x80}[name])
    assert len(result)==3


@pytest.mark.parametrize('name',['eth0','wlan0','tun0','gre0'])
def test_any_active_nonloopback_device_is_unsafe(name):
    with pytest.raises(RuntimeError,match='active external'):
        assert_isolated([(1,'lo'),(2,name)],flags=lambda n:0x49 if n=='lo' else 0x41)


def test_unobservable_network_is_not_isolated():
    with pytest.raises(RuntimeError):assert_isolated([],flags=lambda _:0)
