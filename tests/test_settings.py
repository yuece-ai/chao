import pytest
from chao.settings import REQUIRED, ConfigError, Field, describe, id_list, integer, load, path_map

FIELDS = [Field('workers', integer, 1, 'worker processes'),
          Field('strategies', id_list, (1, 2), 'strategy ids'),
          Field('root', str, REQUIRED, 'data root')]


def test_first_source_wins_and_origin_is_recorded():
    loaded = load(FIELDS, [('gui', {'workers': '8'}), ('file', {'workers': 4, 'root': '/d'})])
    assert loaded.values == {'workers': 8, 'strategies': (1, 2), 'root': '/d'}
    assert loaded.origins == {'workers': 'gui', 'strategies': 'default', 'root': 'file'}
    assert describe(loaded)[0] == "workers = 8  (gui)"


@pytest.mark.parametrize('sources,message', [
    ([('file', {'root': '/d', 'wokers': 2})], "file: unknown keys ['wokers']"),
    ([('file', {})], "missing required setting 'root'"),
    ([('gui', {'root': '/d', 'workers': 'many'})], "gui: bad value 'many' for 'workers'"),
])
def test_config_errors_are_explicit(sources, message):
    with pytest.raises(ConfigError, match=message.replace('[', r'\[').replace(']', r'\]')):
        load(FIELDS, sources)


def test_parsers_accept_gui_strings_and_file_values():
    assert id_list('1, 3,7') == id_list([1, 3, 7]) == (1, 3, 7)
    assert path_map('2=/a,6=/b') == path_map({'2': '/a', '6': '/b'}) == {2: '/a', 6: '/b'}
