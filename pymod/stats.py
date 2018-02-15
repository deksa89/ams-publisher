import socket
import select
import os
import signal
import time
import re

from multiprocessing import Process, Event
from argo_nagios_ams_publisher.shared import Shared

maxcmdlength = 128

class StatSig(object):
    def __init__(self, worker):
        self.laststattime = time.time()
        self.name = worker
        self.msgdo = 'sent' if self._iam_publisher() else 'consumed'
        self._reset()

    def _stat_msg(self, hours):
        nmsg = self.shared.stats['published'] if self._iam_publisher() else self.shared.stats['consumed']
        self.shared.log.info('{0} {1}: {2} {3} msgs in {4:0.2f} hours'.format(self.__class__.__name__,
                                                                              self.name,
                                                                              self.msgdo,
                                                                              nmsg,
                                                                              hours))

    def _reset(self):
        if self._iam_publisher():
            self.shared.stats['published'] = 0
        else:
            self.shared.stats['consumed'] = 0

    def _iam_publisher(self):
        if 'Publish' in self.__class__.__name__:
            return True
        else:
            return False

    def stat_reset(self):
        self._stat_msg(self.shared.general['statseveryhour'])
        self._reset()
        self.laststattime = time.time()

    def stats(self):
        sincelaststat = time.time() - self.laststattime
        self._stat_msg(sincelaststat/3600)


class StatSock(Process):
    def __init__(self, events, sock):
        Process.__init__(self)
        self.events = events
        self.shared = Shared()
        self.sock = sock

        try:
            self.sock.listen(1)
        except socket.error as m:
            self.shared.log.error('Cannot initialize Stats socket %s - %s' % (sockpath, repr(m)))
            raise SystemExit(1)

    def _cleanup(self):
        self.sock.close()
        os.unlink(self.shared.general['statsocket'])
        raise SystemExit(0)

    def parse_cmd(self, cmd):
        m = re.findall('w:\w+\+g:\w+', cmd)
        queries = list()

        if m:
            for c in m:
                w, g = c.split('+')
                w = w.split(':')[1]
                g = g.split(':')[1]
                queries.append((w, g))

        if len(queries) > 0:
            return queries
        else:
            return False

    def answer(self, query):
        a = ''
        for q in query:
            r = self.shared.get_nmsg_interval(q[0], q[1])
            a += 'w:%s+r:%s ' % (str(q[0]), str(r))

        return a[:-1]

    def run(self):
        self.poller = select.poll()
        self.poller.register(self.sock.fileno(), select.POLLIN)

        while True:
            try:
                event = self.poller.poll(float(self.shared.runtime['evsleep'] * 1000))
                if len(event) > 0 and event[0][1] & select.POLLIN:
                    conn, addr = self.sock.accept()
                    data = conn.recv(maxcmdlength)
                    q = self.parse_cmd(data)
                    if q:
                        a = self.answer(q)
                        # self.shared.log.error('Answer %s %d' % (self.shared._stats, id(self.shared._stats['metrics'])))
                        self.shared.log.error('Answer %s' % a)
                if self.events['term-stats'].is_set():
                    self.shared.log.info('Stats received SIGTERM')
                    self.events['term-stats'].clear()
                    self._cleanup()

            except KeyboardInterrupt:
                self._cleanup()