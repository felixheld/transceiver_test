from liteeth.common import *

from litex.soc.interconnect import wishbone
from litex.soc.interconnect.stream_packet import *

from wishbone.packet import user_description


etherbone_magic = 0x4e6f
etherbone_version = 1
etherbone_packet_header_length = 8
etherbone_packet_header_fields = {
    "magic":     HeaderField(0, 0, 16),

    "version":   HeaderField(2, 4,  4),
    "nr":        HeaderField(2, 2,  1),
    "pr":        HeaderField(2, 1,  1),
    "pf":        HeaderField(2, 0,  1),

    "addr_size": HeaderField(3, 4,  4),
    "port_size": HeaderField(3, 0,  4)
}
etherbone_packet_header = Header(etherbone_packet_header_fields,
                                 etherbone_packet_header_length,
                                 swap_field_bytes=True)

etherbone_record_header_length = 4
etherbone_record_header_fields = {
    "bca":         HeaderField(0, 0, 1),
    "rca":         HeaderField(0, 1, 1),
    "rff":         HeaderField(0, 2, 1),
    "cyc":         HeaderField(0, 4, 1),
    "wca":         HeaderField(0, 5, 1),
    "wff":         HeaderField(0, 6, 1),

    "byte_enable": HeaderField(1, 0, 8),

    "wcount":      HeaderField(2, 0, 8),

    "rcount":      HeaderField(3, 0, 8)
}
etherbone_record_header = Header(etherbone_record_header_fields,
                                 etherbone_record_header_length,
                                 swap_field_bytes=True)

def _remove_from_layout(layout, *args):
    r = []
    for f in layout:
        remove = False
        for arg in args:
            if f[0] == arg:
                remove = True
        if not remove:
            r.append(f)
    return r

def eth_etherbone_packet_description(dw):
    param_layout = etherbone_packet_header.get_layout()
    payload_layout = [
        ("data",     dw),
        ("error", dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_etherbone_packet_user_description(dw):
    param_layout = etherbone_packet_header.get_layout()
    param_layout = _remove_from_layout(param_layout,
                                       "magic",
                                       "portsize",
                                       "addrsize",
                                       "version")
    param_layout += user_description(dw).param_layout
    payload_layout = [
        ("data",     dw),
        ("error", dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_etherbone_record_description(dw):
    param_layout = etherbone_record_header.get_layout()
    payload_layout = [
        ("data",     dw),
        ("error", dw//8)
    ]
    return EndpointDescription(payload_layout, param_layout)

def eth_etherbone_mmap_description(dw):
    param_layout = [
        ("we",            1),
        ("count",         8),
        ("base_addr",    32),
        ("be",        dw//8)
    ]
    payload_layout = [
        ("addr", 32),
        ("data", dw)
    ]
    return EndpointDescription(payload_layout, param_layout)


# etherbone packet

class EtherbonePacketPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_etherbone_packet_description(32),
            user_description(32),
            etherbone_packet_header)


class EtherbonePacketTX(Module):
    def __init__(self):
        self.sink = sink = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(user_description(32))

        # # #

        self.submodules.packetizer = packetizer = EtherbonePacketPacketizer()
        self.comb += [
            packetizer.sink.valid.eq(sink.valid),
            packetizer.sink.last.eq(sink.last),
            sink.ready.eq(packetizer.sink.ready),

            packetizer.sink.magic.eq(etherbone_magic),
            packetizer.sink.port_size.eq(32//8),
            packetizer.sink.addr_size.eq(32//8),
            packetizer.sink.pf.eq(sink.pf),
            packetizer.sink.pr.eq(sink.pr),
            packetizer.sink.nr.eq(sink.nr),
            packetizer.sink.version.eq(etherbone_version),

            packetizer.sink.data.eq(sink.data)
        ]
        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            packetizer.source.ready.eq(1),
            If(packetizer.source.valid,
                packetizer.source.ready.eq(0),
                NextState("SEND")
            )
        )
        fsm.act("SEND",
            packetizer.source.connect(source),
            source.length.eq(sink.length + etherbone_packet_header.length),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )


class EtherbonePacketDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            user_description(32),
            eth_etherbone_packet_description(32),
            etherbone_packet_header)


class EtherbonePacketRX(Module):
    def __init__(self):
        self.sink = sink = stream.Endpoint(user_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        self.submodules.depacketizer = depacketizer = EtherbonePacketDepacketizer()
        self.comb += sink.connect(depacketizer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid,
                depacketizer.source.ready.eq(0),
                NextState("CHECK")
            )
        )
        valid = Signal()
        self.sync += valid.eq(
            depacketizer.source.valid &
            (depacketizer.source.magic == etherbone_magic)
        )
        fsm.act("CHECK",
            If(valid,
                NextState("PRESENT")
            ).Else(
                NextState("DROP")
            )
        )
        self.comb += [
            source.last.eq(depacketizer.source.last),

            source.pf.eq(depacketizer.source.pf),
            source.pr.eq(depacketizer.source.pr),
            source.nr.eq(depacketizer.source.nr),

            source.data.eq(depacketizer.source.data),

            source.length.eq(sink.length - etherbone_packet_header.length)
        ]
        fsm.act("PRESENT",
            source.valid.eq(depacketizer.source.valid),
            depacketizer.source.ready.eq(source.ready),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )
        fsm.act("DROP",
            depacketizer.source.ready.eq(1),
            If(depacketizer.source.valid &
               depacketizer.source.last &
               depacketizer.source.ready,
                NextState("IDLE")
            )
        )


class EtherbonePacket(Module):
    def __init__(self, port_sink, port_source):
        self.submodules.tx = tx = EtherbonePacketTX()
        self.submodules.rx = rx = EtherbonePacketRX()
        self.comb += [
            tx.source.connect(port_sink),
            port_source.connect(rx.sink)
        ]
        self.sink, self.source = self.tx.sink, self.rx.source


# etherbone probe

class EtherboneProbe(Module):
    def __init__(self):
        self.sink = sink = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid,
                sink.ready.eq(0),
                NextState("PROBE_RESPONSE")
            )
        )
        fsm.act("PROBE_RESPONSE",
            sink.connect(source),
            source.pf.eq(0),
            source.pr.eq(1),
            If(source.valid & source.last & source.ready,
                NextState("IDLE")
            )
        )

# etherbone record

class EtherboneRecordPacketizer(Packetizer):
    def __init__(self):
        Packetizer.__init__(self,
            eth_etherbone_record_description(32),
            eth_etherbone_packet_user_description(32),
            etherbone_record_header)


class EtherboneRecordDepacketizer(Depacketizer):
    def __init__(self):
        Depacketizer.__init__(self,
            eth_etherbone_packet_user_description(32),
            eth_etherbone_record_description(32),
            etherbone_record_header)


class EtherboneRecordReceiver(Module):
    def __init__(self, buffer_depth=256):
        self.sink = sink = stream.Endpoint(eth_etherbone_record_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_mmap_description(32))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        fifo = stream.SyncFIFO(eth_etherbone_record_description(32), buffer_depth,
                               buffered=True)
        self.submodules += fifo
        self.comb += sink.connect(fifo.sink)

        base_addr = Signal(32)
        base_addr_update = Signal()
        self.sync += If(base_addr_update, base_addr.eq(fifo.source.data))

        counter = Signal(max=512)
        counter_reset = Signal()
        counter_ce = Signal()
        self.sync += \
            If(counter_reset,
                counter.eq(0)
            ).Elif(counter_ce,
                counter.eq(counter + 1)
            )

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            fifo.source.ready.eq(1),
            counter_reset.eq(1),
            If(fifo.source.valid,
                base_addr_update.eq(1),
                If(fifo.source.wcount,
                    NextState("RECEIVE_WRITES")
                ).Elif(fifo.source.rcount,
                    NextState("RECEIVE_READS")
                )
            )
        )
        fsm.act("RECEIVE_WRITES",
            source.valid.eq(fifo.source.valid),
            source.last.eq(counter == fifo.source.wcount-1),
            source.count.eq(fifo.source.wcount),
            source.be.eq(fifo.source.byte_enable),
            source.addr.eq(base_addr[2:] + counter),
            source.we.eq(1),
            source.data.eq(fifo.source.data),
            fifo.source.ready.eq(source.ready),
            If(source.valid & source.ready,
                counter_ce.eq(1),
                If(source.last,
                    If(fifo.source.rcount,
                        NextState("RECEIVE_BASE_RET_ADDR")
                    ).Else(
                        NextState("IDLE")
                    )
                )
            )
        )
        fsm.act("RECEIVE_BASE_RET_ADDR",
            counter_reset.eq(1),
            If(fifo.source.valid,
                base_addr_update.eq(1),
                NextState("RECEIVE_READS")
            )
        )
        fsm.act("RECEIVE_READS",
            source.valid.eq(fifo.source.valid),
            source.last.eq(counter == fifo.source.rcount-1),
            source.count.eq(fifo.source.rcount),
            source.base_addr.eq(base_addr),
            source.addr.eq(fifo.source.data[2:]),
            fifo.source.ready.eq(source.ready),
            If(source.valid & source.ready,
                counter_ce.eq(1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )


class EtherboneRecordSender(Module):
    def __init__(self, buffer_depth=256):
        self.sink = sink = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_record_description(32))

        # # #

        # TODO: optimize ressources (no need to store parameters as datas)
        pbuffer = stream.SyncFIFO(eth_etherbone_mmap_description(32), buffer_depth)
        self.submodules += pbuffer
        self.comb += sink.connect(pbuffer.sink)

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            pbuffer.source.ready.eq(1),
            If(pbuffer.source.valid,
                pbuffer.source.ready.eq(0),
                NextState("SEND_BASE_ADDRESS")
            )
        )
        self.comb += [
            source.byte_enable.eq(pbuffer.source.be),
            If(pbuffer.source.we,
                source.wcount.eq(pbuffer.source.count)
            ).Else(
                source.rcount.eq(pbuffer.source.count)
            )
        ]

        fsm.act("SEND_BASE_ADDRESS",
            source.valid.eq(pbuffer.source.valid),
            source.last.eq(0),
            source.data.eq(pbuffer.source.base_addr),
            If(source.ready,
                NextState("SEND_DATA")
            )
        )
        fsm.act("SEND_DATA",
            source.valid.eq(pbuffer.source.valid),
            source.last.eq(pbuffer.source.last),
            source.data.eq(pbuffer.source.data),
            If(source.valid & source.ready,
                pbuffer.source.ready.eq(1),
                If(source.last,
                    NextState("IDLE")
                )
            )
        )


class EtherboneRecord(Module):
    # Limitation: For simplicity we only support 1 record per packet
    def __init__(self, endianness="big"):
        self.sink = sink = stream.Endpoint(eth_etherbone_packet_user_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_packet_user_description(32))

        # # #

        # receive record, decode it and generate mmap stream
        self.submodules.depacketizer = depacketizer = EtherboneRecordDepacketizer()
        self.submodules.receiver = receiver = EtherboneRecordReceiver()
        self.comb += [
            sink.connect(depacketizer.sink),
            depacketizer.source.connect(receiver.sink)
        ]
        if endianness is "big":
            self.comb += receiver.sink.data.eq(reverse_bytes(depacketizer.source.data))

        # receive mmap stream, encode it and send records
        self.submodules.sender = sender = EtherboneRecordSender()
        self.submodules.packetizer = packetizer = EtherboneRecordPacketizer()
        self.comb += [
            sender.source.connect(packetizer.sink),
            packetizer.source.connect(source),
            # XXX improve this
            source.length.eq(sender.source.wcount*4 + 4 + etherbone_record_header.length)
        ]
        if endianness is "big":
            self.comb += packetizer.sink.data.eq(reverse_bytes(sender.source.data))



# etherbone wishbone

class EtherboneWishboneMaster(Module):
    def __init__(self):
        self.sink = sink = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.source = source = stream.Endpoint(eth_etherbone_mmap_description(32))
        self.bus = bus = wishbone.Interface()

        # # #

        data = Signal(32)
        data_update = Signal()
        self.sync += If(data_update, data.eq(bus.dat_r))

        self.submodules.fsm = fsm = FSM(reset_state="IDLE")
        fsm.act("IDLE",
            sink.ready.eq(1),
            If(sink.valid,
                sink.ready.eq(0),
                If(sink.we,
                    NextState("WRITE_DATA")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )
        fsm.act("WRITE_DATA",
            bus.adr.eq(sink.addr),
            bus.dat_w.eq(sink.data),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.valid),
            bus.we.eq(1),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                sink.ready.eq(1),
                If(sink.last,
                    NextState("IDLE")
                )
            )
        )
        fsm.act("READ_DATA",
            bus.adr.eq(sink.addr),
            bus.sel.eq(sink.be),
            bus.stb.eq(sink.valid),
            bus.cyc.eq(1),
            If(bus.stb & bus.ack,
                data_update.eq(1),
                NextState("SEND_DATA")
            )
        )
        fsm.act("SEND_DATA",
            source.valid.eq(sink.valid),
            source.last.eq(sink.last),
            source.base_addr.eq(sink.base_addr),
            source.addr.eq(sink.addr),
            source.count.eq(sink.count),
            source.be.eq(sink.be),
            source.we.eq(1),
            source.data.eq(data),
            If(source.valid & source.ready,
                sink.ready.eq(1),
                If(source.last,
                    NextState("IDLE")
                ).Else(
                    NextState("READ_DATA")
                )
            )
        )


# etherbone

class Etherbone(Module):
    def __init__(self):
        self.sink = stream.Endpoint(user_description(32))
        self.source = stream.Endpoint(user_description(32))

        # # #

        # decode/encode etherbone packets
        self.submodules.packet = packet = EtherbonePacket(self.source, self.sink)

        # packets can be probe (etherbone discovering) or records with
        # writes and reads
        self.submodules.probe = probe = EtherboneProbe()
        self.submodules.record = record = EtherboneRecord()

        # arbitrate/dispatch probe/records packets
        dispatcher = Dispatcher(packet.source, [probe.sink, record.sink])
        self.comb += dispatcher.sel.eq(~packet.source.pf)
        arbiter = Arbiter([probe.source, record.source], packet.sink)
        self.submodules += dispatcher, arbiter

        # create mmap wishbone master
        self.submodules.master = master = EtherboneWishboneMaster()
        self.comb += [
            record.receiver.source.connect(master.sink),
            master.source.connect(record.sender.sink)
        ]
