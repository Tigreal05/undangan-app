import asyncio, aiohttp, re

async def main():
    jar = aiohttp.CookieJar(unsafe=True)
    async with aiohttp.ClientSession(cookie_jar=jar) as s:
        r = await s.post('http://localhost:9000/admin/login',
                         data={'username': 'admin', 'password': 'RahasiaS0Test!'})
        print("login:", r.status)
        page = await (await s.get('http://localhost:9000/admin')).text()
        tok = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        print("csrf ok:", len(tok) > 20)

        async def up(fname, fpath, ctype):
            d = aiohttp.FormData()
            d.add_field('csrf_token', tok)
            with open(fpath, 'rb') as f:
                d.add_field('file', f, filename=fname, content_type=ctype)
                r = await s.post('http://localhost:9000/admin/upload_media',
                                 data=d, allow_redirects=False)
            print("upload %-24r (%-14s): %s -> %s" % (fname, ctype, r.status,
                  r.headers.get('Location')))

        await up('../../evil.py', '/tmp/evil.py', 'text/x-python')   # harus ditolak (badtype)
        await up('hack.php', '/tmp/malware.jpg', 'image/jpeg')       # harus ditolak (badtype)
        await up('real.png', '/tmp/real.png', 'image/png')           # harus sukses
        await up('foto pengantin.png', '/tmp/real.png', 'image/png') # nama dgn spasi -> disanitasi

loop = asyncio.new_event_loop(); loop.run_until_complete(main())
