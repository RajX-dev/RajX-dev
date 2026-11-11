import datetime
import hashlib
import os
import time
from dateutil import relativedelta
from lxml import etree
import requests

ACCESS_TOKEN = os.environ.get('ACCESS_TOKEN', '')
USER_NAME = os.environ.get('USER_NAME', '')
HEADERS = {'authorization': f'token {ACCESS_TOKEN}'}
QUERY_COUNT = {
    'user_getter': 0,
    'follower_getter': 0,
    'graph_repos_stars': 0,
    'recursive_loc': 0,
    'graph_commits': 0,
    'loc_query': 0,
}
OWNER_ID = None


def _get_headers():
    token = os.environ.get('ACCESS_TOKEN') or ACCESS_TOKEN
    if not token:
        raise RuntimeError('ACCESS_TOKEN environment variable is required.')
    return {'authorization': f'token {token}'}


def _get_username():
    username = os.environ.get('USER_NAME') or USER_NAME
    if not username:
        raise RuntimeError('USER_NAME environment variable is required.')
    return username


def daily_readme(start_date):
    now_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    diff = relativedelta.relativedelta(now_utc, start_date)
    return '{} {}, {} {}, {} {}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
    )


def format_plural(unit):
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables, retries=3):
    """Send a GitHub GraphQL request with timeout and transient-error retries."""
    headers = _get_headers()
    last_error = None

    for attempt in range(retries + 1):
        try:
            response = requests.post(
                'https://api.github.com/graphql',
                json={'query': query, 'variables': variables},
                headers=headers,
                timeout=30,
            )
        except requests.exceptions.RequestException as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            raise RuntimeError(f'{func_name} failed after network error: {exc}') from exc

        if response.status_code == 200:
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict) and payload.get('data') is None and payload.get('errors') and attempt < retries:
                time.sleep(2 ** (attempt + 1))
                continue
            return response

        if response.status_code == 401:
            raise RuntimeError(f'{func_name} failed with 401 Unauthorized. Check ACCESS_TOKEN.')

        if response.status_code in (403, 429, 500, 502, 503, 504) and attempt < retries:
            retry_after = response.headers.get('Retry-After')
            if retry_after and retry_after.isdigit():
                delay = min(int(retry_after), 60)
            elif response.status_code in (403, 429):
                delay = 10 * (attempt + 1)
            else:
                delay = 2 ** (attempt + 1)
            time.sleep(delay)
            continue

        if response.status_code == 403:
            raise RuntimeError(
                f'{func_name} hit GitHub API rate/abuse limit (403): {response.text}'
            )
        raise RuntimeError(
            f'{func_name} failed with status {response.status_code}: {response.text} {QUERY_COUNT}'
        )

    raise RuntimeError(f'{func_name} failed: {last_error}')


def _extract_user(payload, func_name):
    data = payload.get('data') if isinstance(payload, dict) else None
    user = data.get('user') if isinstance(data, dict) else None
    if user is None:
        errors = payload.get('errors', []) if isinstance(payload, dict) else []
        username = _get_username()
        raise RuntimeError(
            f"{func_name}: GitHub API returned null for user '{username}'. "
            f"Check that USER_NAME is correct and the token has the required scopes. "
            f"API errors: {errors}"
        )
    return user


def graph_commits(start_date, end_date):
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date, 'end_date': end_date, 'login': _get_username()}
    request = simple_request(graph_commits.__name__, query, variables)
    user = _extract_user(request.json(), graph_commits.__name__)
    return int(user['contributionsCollection']['contributionCalendar']['totalContributions'])


def graph_repos_stars(count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazerCount
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    total_stars = 0
    current_cursor = cursor

    while True:
        query_count('graph_repos_stars')
        variables = {
            'owner_affiliation': owner_affiliation,
            'login': _get_username(),
            'cursor': current_cursor,
        }
        request = simple_request(graph_repos_stars.__name__, query, variables)
        user = _extract_user(request.json(), graph_repos_stars.__name__)
        repos = user.get('repositories') or {}

        if count_type == 'repos':
            return repos.get('totalCount') or 0

        if count_type == 'stars':
            total_stars += stars_counter(repos.get('edges'))
            page_info = repos.get('pageInfo') or {}
            if not page_info.get('hasNextPage'):
                return total_stars
            current_cursor = page_info.get('endCursor')
        else:
            raise ValueError(f'Unsupported count_type: {count_type}')


def recursive_loc(
    owner,
    repo_name,
    data,
    cache_comment,
    addition_total=0,
    deletion_total=0,
    my_commits=0,
    cursor=None,
    retries=3,
):
    query = '''
    query ($repo_name: String!, $owner: String!, $author_id: ID!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor, author: {id: $author_id}) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    author_id = OWNER_ID['id'] if isinstance(OWNER_ID, dict) else OWNER_ID
    current_cursor = cursor

    try:
        while True:
            query_count('recursive_loc')
            variables = {
                'repo_name': repo_name,
                'owner': owner,
                'author_id': author_id,
                'cursor': current_cursor,
            }
            request = simple_request(recursive_loc.__name__, query, variables, retries=retries)
            payload = request.json()
            repo_data = (payload.get('data') or {}).get('repository')
            if repo_data is None:
                return addition_total, deletion_total, my_commits

            default_branch = repo_data.get('defaultBranchRef')
            if default_branch is None or default_branch.get('target') is None:
                return addition_total, deletion_total, my_commits

            history = default_branch['target'].get('history')
            if not history:
                return addition_total, deletion_total, my_commits

            for edge in history.get('edges') or []:
                node = (edge or {}).get('node') or {}
                author = node.get('author') or {}
                user_id = (author.get('user') or {}).get('id')
                if user_id == author_id:
                    my_commits += 1
                    addition_total += node.get('additions') or 0
                    deletion_total += node.get('deletions') or 0

            page_info = history.get('pageInfo') or {}
            if not history.get('edges') or not page_info.get('hasNextPage'):
                return addition_total, deletion_total, my_commits
            current_cursor = page_info.get('endCursor')
    except Exception:
        force_close_file(data, cache_comment)
        raise


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    for node in history['edges']:
        author = node['node'].get('author') or {}
        if author.get('user') == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']
    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    return recursive_loc(
        owner,
        repo_name,
        data,
        cache_comment,
        addition_total,
        deletion_total,
        my_commits,
        history['pageInfo']['endCursor'],
    )


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=None):
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            defaultBranchRef {
                                target {
                                    ... on Commit {
                                        history {
                                            totalCount
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo {
                    endCursor
                    hasNextPage
                }
            }
        }
    }'''
    all_edges = list(edges) if edges is not None else []
    current_cursor = cursor

    while True:
        query_count('loc_query')
        variables = {
            'owner_affiliation': owner_affiliation,
            'login': _get_username(),
            'cursor': current_cursor,
        }
        request = simple_request(loc_query.__name__, query, variables)
        user = _extract_user(request.json(), loc_query.__name__)
        repos = user.get('repositories') or {}
        valid_edges = [
            edge for edge in (repos.get('edges') or [])
            if edge and (edge.get('node') or {}).get('nameWithOwner')
        ]
        all_edges.extend(valid_edges)

        page_info = repos.get('pageInfo') or {}
        if not page_info.get('hasNextPage'):
            break
        current_cursor = page_info.get('endCursor')

    return cache_builder(all_edges, comment_size, force_cache)


def _cache_filename():
    return 'cache/' + hashlib.sha256(_get_username().encode('utf-8')).hexdigest() + '.txt'


def cache_builder(edges, comment_size, force_cache, loc_add=0, loc_del=0):
    cached = True
    os.makedirs('cache', exist_ok=True)
    filename = _cache_filename()
    default_comment = 'This line is a comment block. Write whatever you want here.\n'

    try:
        with open(filename, 'r') as f:
            lines = f.readlines()
    except FileNotFoundError:
        lines = [default_comment] * comment_size

    if len(lines) < comment_size:
        cache_comment = lines + [default_comment] * (comment_size - len(lines))
        old_entries = []
    else:
        cache_comment = lines[:comment_size]
        old_entries = lines[comment_size:]

    existing_by_hash = {}
    if not force_cache:
        for entry in old_entries:
            parts = entry.split()
            if len(parts) == 5:
                existing_by_hash[parts[0]] = entry if entry.endswith('\n') else entry + '\n'

    data = []
    for edge in edges:
        repo_hash = hashlib.sha256(edge['node']['nameWithOwner'].encode('utf-8')).hexdigest()
        if repo_hash in existing_by_hash:
            data.append(existing_by_hash[repo_hash])
        else:
            cached = False
            data.append(f'{repo_hash} 0 0 0 0\n')

    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)

    for index, edge in enumerate(edges):
        node = edge['node']
        repo_hash = hashlib.sha256(node['nameWithOwner'].encode('utf-8')).hexdigest()
        commit_count = int(data[index].split()[1])

        default_branch = node.get('defaultBranchRef')
        target = default_branch.get('target') if default_branch else None
        history = target.get('history') if target else None
        if not history:
            data[index] = f'{repo_hash} 0 0 0 0\n'
            continue

        total_commits = int(history['totalCount'])
        if commit_count != total_commits:
            owner, repo_name = node['nameWithOwner'].split('/', 1)
            loc = recursive_loc(owner, repo_name, data, cache_comment)
            data[index] = f'{repo_hash} {total_commits} {loc[2]} {loc[0]} {loc[1]}\n'
            force_close_file(data, cache_comment)

    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)

    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    data = []
    if os.path.exists(filename):
        with open(filename, 'r') as f:
            if comment_size > 0:
                data = f.readlines()[:comment_size]
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def force_close_file(data, cache_comment):
    os.makedirs('cache', exist_ok=True)
    filename = _cache_filename()
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)


def stars_counter(data):
    if not data:
        return 0
    total_stars = 0
    for edge in data:
        node = (edge or {}).get('node')
        if not node:
            continue
        if 'stargazerCount' in node and node['stargazerCount'] is not None:
            total_stars += node['stargazerCount']
        else:
            stargazers = node.get('stargazers') or {}
            total_stars += stargazers.get('totalCount') or 0
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data):
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'commit_data', commit_data, 0)
    justify_format(root, 'star_data', star_data, 0)
    justify_format(root, 'repo_data', repo_data, 0)
    justify_format(root, 'contrib_data', contrib_data, 0)
    justify_format(root, 'follower_data', follower_data, 0)
    justify_format(root, 'loc_data', loc_data[2], 0)
    justify_format(root, 'loc_add', loc_data[0], 0)
    justify_format(root, 'loc_del', loc_data[1], 0)
    justify_format(root, 'age_data', age_data, 0)
    tree.write(filename, encoding='utf-8', xml_declaration=True)


def justify_format(root, element_id, new_text, length=0):
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f'{element_id}_dots', dot_string)


def find_and_replace(root, element_id, new_text):
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def commit_counter(comment_size):
    total_commits = 0
    filename = _cache_filename()
    with open(filename, 'r') as f:
        data = f.readlines()
    data = data[comment_size:]
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


def user_getter(username):
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = simple_request(user_getter.__name__, query, variables)
    user = _extract_user(request.json(), user_getter.__name__)
    return {'id': user['id']}, user['createdAt']


def follower_getter(username):
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = simple_request(follower_getter.__name__, query, {'login': username})
    user = _extract_user(request.json(), follower_getter.__name__)
    return int(user['followers']['totalCount'])


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def perf_counter(funct, *args):
    start = time.perf_counter()
    funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


if __name__ == '__main__':
    username = _get_username()
    user_data, user_time = perf_counter(user_getter, username)
    OWNER_ID, acc_date = user_data

    # Calculate account creation date for uptime
    account_creation = datetime.datetime.strptime(acc_date, '%Y-%m-%dT%H:%M:%SZ')
    age_data, age_time = perf_counter(daily_readme, account_creation)

    total_loc, loc_time = perf_counter(loc_query, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
    commit_data, commit_time = perf_counter(commit_counter, 7)
    star_data, star_time = perf_counter(graph_repos_stars, 'stars', ['OWNER'])
    repo_data, repo_time = perf_counter(graph_repos_stars, 'repos', ['OWNER'])
    contrib_data, contrib_time = perf_counter(graph_repos_stars, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
    follower_data, follower_time = perf_counter(follower_getter, username)

    for index in range(len(total_loc) - 1):
        total_loc[index] = '{:,}'.format(total_loc[index])

    svg_overwrite('dark_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
    svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1])
