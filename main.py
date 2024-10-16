# -*- coding: utf-8 -*-
import asyncio
import tomllib
from urllib.parse import urlparse
from pathlib import Path

import httpx

config = Path('./config.toml')
mirrors: list[dict[str, str]] = []

target_base_url = ''
origin_base_url = ''

target_client = httpx.AsyncClient(timeout=None)
origin_client = httpx.AsyncClient(timeout=None)


async def check_target(target) -> list[dict[str, str]]:
    # 检查target是否存在
    resp = await target_client.get(f'{target_base_url}/orgs/{target}')
    assert resp.status_code in [200, 404], f'出了点小问题？{target=}, {resp.status_code=}'
    target_repos = []
    if resp.status_code == 404:
        # 创建org
        resp = await target_client.post(f'{target_base_url}/orgs/', json={'username': target})
        assert resp.status_code == 201, f'创建org应当返回201, {resp.status_code=}'
    else:
        target_repos = await get_target_org_repos(target)
    return target_repos


async def get_target_org_repos(target):
    repos = []
    url = f'{target_base_url}/orgs/{target}/repos'
    while url:
        resp = await target_client.get(url)
        assert resp.status_code == 200, f'获取target_org仓库应当返回200, {resp.status_code=}'
        repos.extend(resp.json())
        if 'next' in resp.links:
            url = resp.links['next'].get('url')
        else:
            url = None
    return repos


async def get_origin_org_repos_iter(origin):
    url = f'{origin_base_url}/orgs/{origin}/repos'
    while url:
        resp = await origin_client.get(url)
        assert resp.status_code == 200, f'获取源org仓库应当返回200, {resp.status_code=}'
        for repo in resp.json():
            yield repo
        if 'next' in resp.links:
            url = resp.links['next'].get('url')
        else:
            url = None


async def update_org(origin, target):
    results = []
    target_repos = await check_target(target)
    target_repo_names = [x['name'] for x in target_repos]
    async for repo in get_origin_org_repos_iter(origin):
        if repo['name'] in target_repo_names:
            target_repo_names.remove(repo['name'])
            print(f"Existed - {target}/{repo['name']}")
            results.append({'code': 'existed', 'name': repo['name']})
            continue
        resp = await target_client.post(
            f'{target_base_url}/repos/migrate/',
            json={
                'clone_addr': repo['clone_url'],
                'mirror': True,
                'repo_name': repo['name'],
                'repo_owner': target,
            }
        )
        if resp.status_code == 201:
            print(f"Created - {target}/{repo['name']}")
            results.append({'code': 'created', 'name': repo['name']})
        else:
            print(f"CreateFailed - {target}/{repo['name']}: {resp.text}")
            results.append({'code': 'fail', 'operation': 'create', 'name': repo['name'], 'message': resp.text})
    # 删除不存在的repo
    for repo_name in target_repo_names:
        resp = await target_client.delete(f'{target_base_url}/repos/{target}/{repo_name}/')
        if resp.status_code == 204:
            print(f"Deleted - {target}/{repo_name}")
            results.append({'code': 'deleted', 'name': repo_name})
        else:
            print(f"DeleteFailed - {target}/{repo_name}: {resp.text}")
            results.append({'code': 'delete-failed', 'name': repo_name, 'message': resp.text})
    return results


async def update_repo(origin, origin_url, target):
    parsed = urlparse(origin_url)
    assert bool(parsed.scheme and parsed.netloc), 'repo 的 origin_url 应当是一个合法的 url'
    target_repos = await check_target(target)
    target_repo_names = [x['name'] for x in target_repos]
    if origin in target_repo_names:
        return {'code': 'exists', 'name': origin}
    resp = await target_client.post(
        f'{target_base_url}/repos/migrate/',
        json={
            'clone_addr': origin_url,
            'mirror': True,
            'repo_name': origin,
            'repo_owner': target,
        }
    )
    if resp.status_code == 201:
        print(f"Created - {origin}")
        return {'code': 'created', 'name': origin}
    else:
        print(f"CreateFailed - {origin}: {resp.text}")
        return {'code': 'create-failed', 'name': origin, 'message': resp.text}


async def update_mirror(_mirror):
    assert _mirror.get('type') in ['repo', 'org'], '类型必须为repo或者org'
    assert _mirror.get('origin'), '镜像源名字不为空'
    if not _mirror.get('target'):
        _mirror['target'] = _mirror['origin']
    # assert _mirror.get('target'), '镜像目标名字不为空'
    if _mirror['type'] == 'repo':
        print(f'---更新Repo:{_mirror["origin"]}---')
        await update_repo(_mirror['origin'], _mirror.get('url'), _mirror['target'])
        # print(result)
    elif _mirror['type'] == 'org':
        print(f'---更新Org:{_mirror["origin"]}---')
        await update_org(_mirror['origin'], _mirror['target'])


async def main():
    global target_base_url, origin_base_url, target_client
    if not config.exists():
        raise FileNotFoundError("没有找到配置文件")
    data = tomllib.load(config.open('rb'))

    assert data.get('config'), '配置文件应当包含 config'

    target_base_url = data['config'].get('target_base_url')
    origin_base_url = data['config'].get('origin_base_url')

    assert bool(target_base_url) and bool(origin_base_url), 'config 下应当包含 target_base_url 和 origin_base_url'

    token = data['config'].get('token')
    assert token, 'config 下应当包含 token 用于认证'
    target_client = httpx.AsyncClient(timeout=None, headers={
        'Authorization': f'token {token}'
    })
    resp = await target_client.get(f"{target_base_url}/user")
    assert resp.status_code == 200, '认证成功后应当能正确获取当前用户数据'
    assert data.get('mirrors'), '配置文件应当包含 mirrors'
    for mirror in data['mirrors']:
        try:
            await update_mirror(mirror)
        except AssertionError as e:
            print(f"origin: {mirror.get('origin')} 同步失败，{e=}")


if __name__ == '__main__':
    asyncio.run(main())
