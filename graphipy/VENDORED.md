# GraphiPy (vendored)

Source: https://github.com/shobeir/GraphiPy @ `684d150` (master, 2019-05-02)
License: MIT — see `LICENSE` in this folder.

Vendored as a source copy rather than installed with pip, because:

- the GitHub repo ships **no `setup.py` / `pyproject.toml`**, so
  `pip install git+https://github.com/shobeir/GraphiPy` cannot work;
- `files.pythonhosted.org` does not resolve on this machine, so
  `pip install GraphiPy` (PyPI 0.0.1) cannot download either.

## Local patches

The upstream code is from 2019 and breaks on current pandas/networkx.
Two fixes were applied to the vendored copy:

| File | Change | Reason |
|---|---|---|
| `graph/graph_pandas.py` | `DataFrame.append(...)` → `pd.concat([...])` (2x) | `DataFrame.append` was removed in pandas 2.0 (installed: 2.3.3) |
| `exportnx.py` | `nx_graph.node[...]` → `nx_graph.nodes[...]` (4x) | `Graph.node` was removed in networkx 2.4 (installed: 3.4.2) |

Re-pulling from upstream will reintroduce both breakages.

## What works now

Verified against pandas 2.3.3 / networkx 3.4.2:

- `graph.graph_dict.DictGraph`, `graph.graph_pandas.PandasGraph`
- `graph.graph_base.BaseNode`, `BaseEdge`
- `export_all_csv()` / `export_csv()`
- `exportnx.ExportNX.create_from_dict()`, `create_from_pd()`, `create_from_csv()`

`create_from_csv(path)` needs a **trailing separator** (`"csv/demo/"`), and
`PandasGraph()` creates a `csv/` folder in the current working directory on
construction.

## What does NOT work yet

`graphipy.graphipy.GraphiPy` imports every social API module at the top, so
importing it pulls in packages that are not installed (and cannot be
downloaded while pythonhosted is unreachable). Import the submodules above
directly instead.

Needed for the API modules / Neo4j, none of them installed:

    pytumblr httplib2 google-api-python-client google-auth
    google-auth-oauthlib google-auth-httplib2 oauth2client
    facebook-sdk py2neo

Note `networkx` is required by `exportnx.py` but is missing from upstream's
`requirements.txt`; it is already installed here.
