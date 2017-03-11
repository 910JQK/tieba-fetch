#!/usr/bin/env python3


import re
import sys
import json
import argparse
import urllib.request
from urllib.parse import urlencode, unquote
from bs4 import BeautifulSoup
from bs4.element import *


URL_M = 'http://tieba.baidu.com/mo/m'
URL_FLR = 'http://tieba.baidu.com/mo/m/flr'
USER_PREFIX = 'i?un='
EMOTICON_URL_PREFIX = 'http://tb2.bdstatic.com/tb/editor/images/'
GATE_URL_PREFIX = 'http://gate.baidu.com'
FLOOR_STR = '楼. '


quiet = False
preserve_imgsrc_url = False


def remove_prefix(string, prefix):
    found = string.find(prefix)
    if found != -1:
        return string[found+len(prefix):]
    else:
        return string


def info(*args, **kwargs):
    if not quiet:
        print(*args, file=sys.stderr, **kwargs)


def gen_url(url, **kwargs):
    return url + '?' + urlencode(kwargs)


def get_total_pages(doc):
    pn_input = doc.find('input', attrs={'name': 'pnum'})
    if pn_input:
        return int(pn_input['value'])
    else:
        return 1


def fetch(url, **kwargs):
    url = gen_url(url, **kwargs)
    info('【請求】%s' % url)
    response = urllib.request.urlopen(url)
    return BeautifulSoup(response.read(), 'lxml')


def get_tag_text(tag):
    if tag.name == 'br':
        return '\n'
    elif isinstance(tag, NavigableString):
        return tag.string
    elif tag.get('href', '').startswith(USER_PREFIX):
        # 樓中樓回覆樓中樓
        return tag.string
    elif tag.get('href', '').startswith(GATE_URL_PREFIX):
        # URL
        return unquote(re.search('src=([^&]+)', tag['href']).group(1))
    elif (
            tag.name == 'img'
            and tag.get('src', '').startswith(EMOTICON_URL_PREFIX)
    ):
        # 表情
        return ' [%s] ' % remove_prefix(tag['src'], EMOTICON_URL_PREFIX)
    elif tag.find('img', class_='BDE_Image'):
        # 圖片
        img = tag.find('img', class_='BDE_Image')
        if preserve_imgsrc_url:
            img_url = unquote(re.search('src=([^&]+)', img['src']).group(1))
        else:
            img_url = img['src']
            img_url = re.sub('quality=[0-9][0-9]', 'quality=100', img_url)
            img_url = (
                re.sub(
                    'size=b[0-9]{2,4}_[0-9]{2,4}', 'size=b2000_2000', img_url
                )
            )
        return '\n%s\n' % img_url
    elif tag.name == 'img':
        return ' %s ' % tag.get('src', '')
    else:
        return ''


def fetch_flr(kz, pid):
    def process_doc(doc):
        items = doc.find_all('div', class_='i')
        subposts = []
        for item in items:
            subpost = {'text': ''}
            subpost['author'] = (
                item.find_all('a', href=re.compile(USER_PREFIX))[-1].string
            )
            subpost['date'] = item.find('span', class_='b').string
            for tag in item.contents:
                if tag.name == 'br':
                    # 文本與其它信息的分界
                    break
                subpost['text'] += get_tag_text(tag)
            subposts.append(subpost)
        return subposts
    info('【抓取樓中樓】%d' % pid)
    doc = fetch(URL_FLR, kz=kz, pid=pid)
    total_pages = get_total_pages(doc)
    info('【頁數】%d' % total_pages)
    subposts = process_doc(doc)
    pn = 2
    while pn <= total_pages:
        info('【換頁】抓取第 %d 頁' % pn)
        subposts += process_doc(fetch(URL_FLR, kz=kz, pid=pid, pnum=pn))
        pn += 1
    info('【完成】樓中樓 %d 抓取完成，共 %d 條回覆' % (pid, len(subposts)))
    return subposts


def fetch_kz(kz):
    def process_doc(doc, pn=1):
        def remove_floor_str(item):
            string = item.contents[0].string
            item.contents[0].string = remove_prefix(string, FLOOR_STR)
        items = doc.find_all('div', class_='i')
        posts = []
        for item in items:
            post = {'text': ''}
            post['author'] = item.find('span', class_='g').string
            post['date'] = item.find('span', class_='b').string
            # 取得 PID 和回覆數
            post['reply_count'] = 0
            reply_link = item.find('a', class_='reply_to')
            if reply_link:
                post['pid'] = int(
                    re.search('pid=([0-9]+)', reply_link['href']).group(1)
                )
                match = re.search('[0-9]+', reply_link.string)
                if match:
                    post['reply_count'] = int(match.group(0))
            else:
                post['pid'] = None
            # 保存樓層信息到 post['floor'], 並在正文中刪除之
            fc_str = item.contents[0].string
            post['floor'] = int(re.match('^[0-9]+', fc_str).group(0))
            remove_floor_str(item)
            # 處理長文拆分
            next_btn = item.find('a', text='下一段')
            if next_btn:
                info('【展開】展開被拆分的第 %d 樓' % post['floor'])
                expand_doc = fetch(
                    URL_M, kz=kz, pnum=pn, expand=post['floor'],
                    **{'global': 1}
                )
                item = expand_doc.find('div', class_='i')
                remove_floor_str(item)
            # 內容
            for tag in item.contents:
                post['text'] += get_tag_text(tag)
            # 樓中樓
            if post['reply_count'] > 0:
                info(
                    '【回覆】第 %(floor)d 樓有 %(reply_count)d 條回覆'
                    % post
                )
                post['subposts'] = fetch_flr(kz=kz, pid=post['pid'])
            # 添加到 list
            posts.append(post)
        return posts
    info('【抓取帖子】%d' % kz)
    doc = fetch(URL_M, kz=kz)
    title = doc.title.string
    info('【標題】%s' % title)
    total_pages = get_total_pages(doc)
    info('【頁數】%d' % total_pages)
    posts = process_doc(doc)
    pn = 2
    while pn <= total_pages:
        info('【換頁】抓取第 %d 頁' % pn)
        posts += process_doc(fetch(URL_M, kz=kz, pnum=pn), pn=pn)
        pn += 1
    info('【完成】帖子 %d 抓取完成，共 %d 層' % (kz, len(posts)))
    return {'title': title, 'author': posts[0]['author'], 'posts': posts}


def fetch_kw(kw, page_start, page_end, dist=False):
    assert page_start <= page_end
    info(
        '【抓取帖子列表】%s吧 %s [%d, %d]'
        % (kw, {True: '精品區', False: ''}[dist], page_start, page_end)
    )
    topics = []
    for pn in range(page_start, page_end+1):
        info('【頁碼】第 %d 頁' % pn)
        if dist:
            doc = fetch(URL_M, kw=kw, pnum=pn, lm=4)
        else:
            doc = fetch(URL_M, kw=kw, pnum=pn)
        for item in doc.find_all('div', class_='i'):
            link = item.find('a')
            kz = int(re.search('kz=([0-9]+)', link['href']).group(1))
            title = remove_prefix(link.string, '.\xA0')
            dist = bool(item.find(text='精'))
            pin = bool(item.find(text='顶'))
            topics.append({'kz': kz, 'title': title, 'dist': dist, 'pin': pin})
    info('【完成】帖子列表抓取完成，共 %d 帖' % len(topics))
    return topics


def print_topic_text(topic):
    print(topic['title'])
    print('LZ: %s' % topic['author'])
    print('='*80)
    for post in topic['posts']:
        print('%(floor)dL | %(author)s | %(date)s' % post)
        print('')
        print(post['text'])
        for subpost in post.get('subposts', []):
            print('*** %(author)s | %(date)s' % subpost)
            print('> %s' % subpost['text'])
            print('')
        print('#'*80)


def output_topic(kz, format):
    if format == 'json':
        print(json.dumps(fetch_kz(kz)) )
    elif format == 'text':
        print_topic_text(fetch_kz(kz))
    else:
        info('Invalid Output Format Specified.')


def output_list(kw, start, end, dist, format):
    if format == 'json':
        print(json.dumps(fetch_kw(kw, start, end, dist)) )
    elif format == 'text':
        for topic in fetch_kw(kw, start, end, dist):
            line = '%(kz)d %(title)s' % topic
            if topic['dist']:
                line += ' 【精】'
            if topic['pin']:
                line += ' 【顶】'
            print(line)
    else:
        info('Invalid Output Format Specified.')


def main():
    global quiet
    global preserve_imgsrc_url
    
    if len(sys.argv) in range(1,3):
        sys.argv.append('--help')

    parser = argparse.ArgumentParser(description='Tieba Post Fetch Tool')
    parser.add_argument(
        '-f', '--format', metavar='輸出格式', choices=['text', 'json'],
        help='(text|json) [default: text]', default='text'
    )
    parser.add_argument(
        '-q', '--quiet', action='store_true', help='不輸出 Debug Message',
        default = False
    )

    subparsers = parser.add_subparsers()

    p_topic = subparsers.add_parser('topic', help='抓取主題帖內容')
    p_topic.add_argument('kz', type=int, help='帖子號碼')
    p_topic.add_argument('-i', '--imgsrc', action='store_true', help='使用 imgsrc.baidu.com 的圖片 URL', default=False)
    p_topic.set_defaults(func=lambda args: output_topic(args.kz, args.format))

    p_list = subparsers.add_parser('list', help='抓取帖子列表')
    p_list.add_argument('kw', help='帖吧名')
    p_list.add_argument('start', type=int, help='起始頁碼')
    p_list.add_argument('end', type=int, help='終止頁碼')
    p_list.add_argument(
        '-d', '--dist', action='store_true', help='只列出精品帖', default=False
    )
    p_list.set_defaults(
        func = (
            lambda args: output_list(
                args.kw, args.start, args.end, args.dist, args.format
            )
        )
    )

    args = parser.parse_args()
    quiet = args.quiet
    if hasattr(args, 'imgsrc'):
        preserve_imgsrc_url = args.imgsrc
    args.func(args)


if __name__ == '__main__':
    main()
