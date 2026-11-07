# Bubsy Music Format MIDI dumper
# programmed by bekzii

import os
import struct
import sys
from pathlib import Path
from typing import BinaryIO
from enum import IntEnum

TICKDIV = 5 # NTSC
MIDI_TICKBASE = 960
MICROSECONDS_PER_MINUTE = 60000000.0

MUSIC_TBL_ADDR = 0x1509A2
MUSIC_TBL_NUM = 56

# tempos aren't stored with the music so i have to write these all out manually
BUBSY_BPMS = {
    0: 160, # title music
    1: 180,
    2: 135,
    3: 170,
    4: 120,
    5: 175, # world one
    6: 190,
    7: 170,
    9: 175, # slipping and falling
    13: 145,
    16: 180,
    17: 138,
    24: 138, # wild wool west
    25: 164,
    29: 164,
    32: 160,
    33: 180,
    41: 130,
    49: 130,
}

class SeqMsgEvent(IntEnum):
    NOTE_OFF = 0
    NOTE_ON = 1
    LOOP_MARK = 2
    PROGRAM_SET = 4
    UNKNOWN = 6
    END_OF_TRACK = 7
    TRACK_NAME = 0xFF # not used in game - only by MidiMsg

    INVALID = -1
    default = UNKNOWN
    #
    @classmethod
    def _missing_(cls, value):
        return cls.INVALID

class SeqMsg:
    event : SeqMsgEvent
    channel : int
    note : int
    velo : int
    loop_end : bool
    program : int
    ticks : int
    delay : int
    def __init__(self) -> None:
        self.event = SeqMsgEvent.INVALID
        self.channel = 0
        self.note = 0
        self.velo = 0
        self.loop_end = False
        self.program = 0
        self.ticks = 0
        self.delay = 0

class MidiMsg:
    event : SeqMsgEvent
    channel : int
    note : int
    velo : int
    loop_end : bool
    program : int
    time : float # seconds
    delay : float
    text : str
    def __init__(self) -> None:
        self.event = SeqMsgEvent.INVALID
        self.channel = 0
        self.note = 0
        self.velo = 0
        self.loop_end = False
        self.program = 0
        self.time = 0
        self.delay = 0
        self.text = ""
class MidiTrack:
    msgs : list[MidiMsg]
    channel : int

    def __init__(self) -> None:
        self.msgs = []
        self.channel = 0

class SeqRecord:
    msgs : list[SeqMsg]
    midi_tracks : list[MidiTrack]
    insts : list[int]
    bpm : float
    name : str
    def __init__(self) -> None:
        self.msgs = []
        self.midi_tracks = []
        self.insts = []
        self.bpm = 0.0
        self.name = ""

def abort():
    print("break!.")
    exit(1)

def read_seq_addrs(f:BinaryIO) -> list[tuple[int, int]]:
    ptrs = []
    addrs = []

    f.seek(MUSIC_TBL_ADDR) # this is the start of the sequence pointer table
    for i in range(MUSIC_TBL_NUM):
        addr = struct.unpack(">I", f.read(4))[0]
        if (addr == 0): continue
        if (addr not in addrs):
            ptrs.append((addr, i))
            addrs.append(addr)

    return ptrs

def read_vlq(f:BinaryIO) -> int:
    a = struct.unpack("B", f.read(1))[0]
    if (a & 0x80): 
        b = struct.unpack("B", f.read(1))[0]
        return (a & 0x7F) | (b << 7)
    return a

def read_msg(f:BinaryIO) -> SeqMsg:
    msg = SeqMsg()
    msg_wk = struct.unpack("B", f.read(1))[0]

    msg.channel = msg_wk & 0xF
    msg.event = SeqMsgEvent((msg_wk & 0x70) >> 4)

    if (msg.event == SeqMsgEvent.NOTE_ON):
        msg.note = struct.unpack("B", f.read(1))[0]
        msg.velo = struct.unpack("B", f.read(1))[0]
    elif (msg.event == SeqMsgEvent.NOTE_OFF):
        msg.note = struct.unpack("B", f.read(1))[0]
    elif (msg.event == SeqMsgEvent.LOOP_MARK):
        msg.loop_end = struct.unpack("B", f.read(1))[0] != 0
    elif (msg.event == SeqMsgEvent.PROGRAM_SET):
        msg.program = struct.unpack("B", f.read(1))[0]
    elif (msg.event == SeqMsgEvent.UNKNOWN):
        f.seek(1, 1)

    # End of track event handler doesn't read the delay
    if (msg_wk & 0x80 and msg.event != SeqMsgEvent.END_OF_TRACK):
        # has delay
        msg.delay = read_vlq(f)

    return msg

def read_seq(f:BinaryIO, addr:int, song_i:int):
    seq = SeqRecord()

    f.seek(addr)
    seq_size, seq_off, inst_off, inst_size = struct.unpack(">H2xB8xHB", f.read(0x10))

    f.seek(addr+seq_off)
    data_start = f.tell()

    tick_pos = 0
    while (f.tell()-data_start < seq_size):
        msg = read_msg(f)
        msg.ticks = tick_pos
        tick_pos += msg.delay
        seq.msgs.append(msg)
        if (msg.event == SeqMsgEvent.END_OF_TRACK): break

    # post-process messages so that they're good in MIDI
    tick_pos = 0
    track_i:dict[str, MidiTrack] = {}
    track_progs = [-1 for _ in range(16)]
    # so that program changes only kick in right when they're needed and don't screw anything up
    track_progs_queued = [-1 for _ in range(16)]

    # scan ahead to see what the first program change event is
    # so that there aren't any issues caused by mismatched event orders
    for i,msg in enumerate(seq.msgs):
        if (msg.event == SeqMsgEvent.PROGRAM_SET and track_progs[msg.channel] == -1):
            track_progs[msg.channel] = msg.program
            track_progs_queued[msg.channel] = msg.program

    # split the messages by channel and program so that it's easier to reason with in a DAW
    for i,msg in enumerate(seq.msgs):
        midi_msg = MidiMsg()
        midi_msg.event = msg.event
        midi_msg.channel = msg.channel            
        midi_msg.note = msg.note
        midi_msg.velo = msg.velo
        midi_msg.loop_end = msg.loop_end
        midi_msg.program = msg.program

        midi_msg.time = tick_pos / TICKDIV / 60
        tick_pos += msg.delay

        if (msg.event == SeqMsgEvent.PROGRAM_SET):
            track_progs_queued[msg.channel] = msg.program
            continue

        if (msg.event == SeqMsgEvent.NOTE_ON):
            track_progs[msg.channel] = track_progs_queued[msg.channel]

        track_key = f"{midi_msg.channel}-{track_progs[msg.channel]}"
        if (track_key not in track_i):
            new_track = MidiTrack()
            new_track.channel = msg.channel

            # insert track initialization events
            prog_msg = MidiMsg()
            prog_msg.event = SeqMsgEvent.PROGRAM_SET
            # there are some songs that don't set the program before playing a note... what?
            prog_msg.program = 0 if (track_progs[msg.channel] == -1) else track_progs[msg.channel]
            prog_msg.channel = msg.channel
            prog_msg.time = 0

            tn_msg = MidiMsg()
            tn_msg.event = SeqMsgEvent.TRACK_NAME
            if (new_track.channel != 9):
                tn_msg.text = f"track {len(seq.midi_tracks)} (chn {new_track.channel})"
            else:
                tn_msg.text = f"track {len(seq.midi_tracks)} (drums)"
            tn_msg.channel = msg.channel
            tn_msg.time = 0

            new_track.msgs.append(prog_msg)
            new_track.msgs.append(tn_msg)

            seq.midi_tracks.append(new_track)
            track_i[track_key] = new_track

        track_i[track_key].msgs.append(midi_msg)

    for track in seq.midi_tracks:
        # make sure all tracks end with an end of track event
        if (track.msgs[-1].event != SeqMsgEvent.END_OF_TRACK):
            end_msg = MidiMsg()
            end_msg.event = SeqMsgEvent.END_OF_TRACK
            end_msg.note = 0xFF # just to see if this is one i placed
            end_msg.time = tick_pos
            track.msgs.append(end_msg)

        # get proper delay value for the track
        for i,msg in enumerate(track.msgs):
            if (i == 0): continue
            msg.delay = msg.time - track.msgs[i-1].time

    f.seek(addr+inst_off)
    for i in range(inst_size):
        seq.insts.append(struct.unpack("B", f.read(1))[0])

    seq.bpm = BUBSY_BPMS[song_i]

    return seq

def write_midi_vlq(f:BinaryIO, v:int):
    if (v >= 0x3FFF): f.write(struct.pack("BBB", ((v >> 14) & 0x7F) | 0x80, ((v >> 7) & 0x7F) | 0x80, v & 0x7F))
    elif (v >= 0x7F): f.write(struct.pack("BB", ((v >> 7) & 0x7F) | 0x80, v & 0x7F))
    else: f.write(struct.pack("B", v & 0x7F))

def write_midi_24(f:BinaryIO, v:int):
    f.write(struct.pack(">I", v)[1:4])

def write_seq_midi(seq:SeqRecord, out_path:Path):
    with open(out_path, "wb") as f:
        f.write(b"MThd")
        f.write(struct.pack(">I", 6)) # section length
        f.write(struct.pack(">HHH", 1, len(seq.midi_tracks), MIDI_TICKBASE)) # format (multiple), tracks, tick div

        for i,track in enumerate(seq.midi_tracks):
            f.write(b"MTrk")
            seclen_pos = f.tell()
            f.write(b"!!!!") # this will be patched after all messages are written

            for msg in track.msgs:
                write_midi_vlq(f, round(msg.delay * (seq.bpm / 60) * MIDI_TICKBASE))

                if (msg.event == SeqMsgEvent.NOTE_OFF):
                    f.write(struct.pack("BBB", 0x80+msg.channel, msg.note, 0))
                elif (msg.event == SeqMsgEvent.NOTE_ON):
                    f.write(struct.pack("BBB", 0x90+msg.channel, msg.note, msg.velo))
                elif (msg.event == SeqMsgEvent.LOOP_MARK):
                    f.write(b"\xFF\x06") # MARKER meta event
                    if (msg.loop_end):
                        text = "Loop End"
                    else:
                        text = "Loop Start"
                    write_midi_vlq(f, len(text))
                    f.write(text.encode("ascii"))
                elif (msg.event == SeqMsgEvent.PROGRAM_SET):
                    f.write(struct.pack("BB", 0xC0+msg.channel, msg.program))
                elif (msg.event == SeqMsgEvent.END_OF_TRACK):
                    f.write(b"\xFF\x2F\x00") # END OF TRACK meta event
                elif (msg.event == SeqMsgEvent.TRACK_NAME):
                    f.write(b"\xFF\x03") # SEQUENCE NAME meta event
                    write_midi_vlq(f, len(msg.text))
                    f.write(msg.text.encode("ascii"))
                    
                    if (i == 0):
                        # shove the tempo in after the last setup event for compatibility
                        write_midi_vlq(f, 0)
                        f.write(b"\xFF\x51\x03") # TEMPO meta event
                        write_midi_24(f, round(MICROSECONDS_PER_MINUTE / seq.bpm))

            # patch in the section header
            sec_end_pos = f.tell()
            f.seek(seclen_pos)
            f.write(struct.pack(">I", sec_end_pos - (seclen_pos+4)))
            f.seek(sec_end_pos)

if (__name__ == "__main__"):
    print("Bubsy music MIDI dumper")
    if (len(sys.argv) < 2):
        print("Please enter the path to the BUBSY USA GENESIS ROM as the command line")
        exit()

    print(f'CONV {sys.argv[1]}')

    in_file = Path(sys.argv[1])
    out_folder = Path.cwd() / in_file.stem

    os.makedirs(name=out_folder, exist_ok=True)

    seqs:list[SeqRecord] = []

    with open(in_file, "rb") as f:
        f.seek(0x120)
        id = f.read(0x10).decode()
        if (id != "Bubsy           "):
            print("That ain't Bubsy")
            print("Sorry man")
            abort()

        addrs = read_seq_addrs(f)
        for i,addr in enumerate(addrs):
            seq = read_seq(f, addr[0], addr[1])
            seq.name = f"BUBSY{addr[1]}"
            seqs.append(seq)

    for i,seq in enumerate(seqs):
        out_seq_file = out_folder / f"{seq.name}.MID"
        print(f"export {out_seq_file}")
        write_seq_midi(seq, out_seq_file)

    exit(0)
