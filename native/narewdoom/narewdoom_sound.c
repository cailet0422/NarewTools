// NarewDoom sound: DOOM sound effects through waveOut (own 16-channel mixer, 44.1 kHz stereo) and music through
// the Windows MIDI synthesizer (MUS lumps -> MIDI with Chocolate Doom's mus2mid, played by a small sequencer
// thread).  Both pause while DOOM is not on screen.
//
// Copyright (C) 2026 NarewTools contributors.  GPL-2.0-or-later (see LICENSE).

#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <mmsystem.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <stdio.h>

#include "doomgeneric/doomtype.h"
#include "doomgeneric/i_sound.h"
#include "doomgeneric/deh_str.h"
#include "doomgeneric/m_misc.h"
#include "doomgeneric/memio.h"
#include "doomgeneric/mus2mid.h"
#include "doomgeneric/w_wad.h"
#include "doomgeneric/z_zone.h"
#include "narewdoom.h"

// m_config.c binds these when FEATURE_SOUND is on (no libsamplerate here)
int use_libsamplerate = 0;
float libsamplerate_scale = 0.65f;

static volatile LONG s_paused;
static volatile LONG s_stat_sfx, s_stat_midi, s_stat_songs, s_stat_buffers;

// ================================================================== sound effects
#define RATE        44100
#define NCHAN       16
#define BUF_FRAMES  1024                        // ~23 ms per buffer
#define NBUF        4
#define SFX_GAIN    0.75f                       // a bit under full scale: the game's own sound plays too

typedef struct {
    uint8_t *data;                              // unsigned 8-bit mono
    uint32_t len;
    uint32_t rate;
} sample_t;

typedef struct {
    const sample_t *s;
    uint64_t pos;                               // 32.32 fixed point position in samples
    uint64_t step;
    int left, right;                            // 0..255
} channel_t;

static channel_t s_chan[NCHAN];
static CRITICAL_SECTION s_mix_lock;
static HWAVEOUT s_wave;
static HANDLE s_wave_event, s_mix_thread;
static WAVEHDR s_hdr[NBUF];
static int16_t s_pcm[NBUF][BUF_FRAMES * 2];
static boolean s_sfx_ok, s_use_prefix;

static void mix(int16_t *out)
{
    int32_t acc[BUF_FRAMES * 2];
    int i, c;
    memset(acc, 0, sizeof(acc));
    EnterCriticalSection(&s_mix_lock);
    if (!s_paused) {
        for (c = 0; c < NCHAN; c++) {
            channel_t *ch = &s_chan[c];
            if (!ch->s)
                continue;
            for (i = 0; i < BUF_FRAMES; i++) {
                uint32_t p = (uint32_t)(ch->pos >> 32);
                int v;
                if (p >= ch->s->len) {
                    ch->s = NULL;
                    break;
                }
                v = ((int)ch->s->data[p] - 128) << 8;
                acc[2 * i] += v * ch->left / 255;
                acc[2 * i + 1] += v * ch->right / 255;
                ch->pos += ch->step;
            }
        }
    }
    LeaveCriticalSection(&s_mix_lock);
    s_stat_buffers++;
    for (i = 0; i < BUF_FRAMES * 2; i++) {
        int32_t v = NarewDoom_Silent() ? 0 : (int32_t)(acc[i] * SFX_GAIN);
        out[i] = (int16_t)(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
    }
}

static DWORD WINAPI mix_thread(LPVOID arg)
{
    int i;
    (void)arg;
    for (i = 0; i < NBUF; i++) {
        mix(s_pcm[i]);
        waveOutWrite(s_wave, &s_hdr[i], sizeof(WAVEHDR));
    }
    for (;;) {
        WaitForSingleObject(s_wave_event, 50);
        for (i = 0; i < NBUF; i++) {
            if (s_hdr[i].dwFlags & WHDR_DONE) {
                mix(s_pcm[i]);
                waveOutWrite(s_wave, &s_hdr[i], sizeof(WAVEHDR));
            }
        }
    }
}

static boolean Sfx_Init(boolean use_sfx_prefix)
{
    WAVEFORMATEX f;
    int i;
    MMRESULT r;
    s_use_prefix = use_sfx_prefix;
    InitializeCriticalSection(&s_mix_lock);
    memset(&f, 0, sizeof(f));
    f.wFormatTag = WAVE_FORMAT_PCM;
    f.nChannels = 2;
    f.nSamplesPerSec = RATE;
    f.wBitsPerSample = 16;
    f.nBlockAlign = 4;
    f.nAvgBytesPerSec = RATE * 4;
    s_wave_event = CreateEventW(NULL, FALSE, FALSE, NULL);
    r = waveOutOpen(&s_wave, WAVE_MAPPER, &f, (DWORD_PTR)s_wave_event, 0, CALLBACK_EVENT);
    if (r != MMSYSERR_NOERROR) {
        NarewDoom_Log("sound effects: waveOutOpen failed (%u)", r);
        return false;
    }
    for (i = 0; i < NBUF; i++) {
        memset(&s_hdr[i], 0, sizeof(WAVEHDR));
        s_hdr[i].lpData = (LPSTR)s_pcm[i];
        s_hdr[i].dwBufferLength = sizeof(s_pcm[i]);
        waveOutPrepareHeader(s_wave, &s_hdr[i], sizeof(WAVEHDR));
    }
    s_mix_thread = CreateThread(NULL, 0, mix_thread, NULL, 0, NULL);
    SetThreadPriority(s_mix_thread, THREAD_PRIORITY_ABOVE_NORMAL);
    s_sfx_ok = true;
    NarewDoom_Log("sound effects: waveOut %d Hz", RATE);
    return true;
}

static void Sfx_Shutdown(void) {}

static void sfx_lump_name(sfxinfo_t *sfx, char *buf, size_t n)
{
    if (sfx->link)
        sfx = sfx->link;
    if (s_use_prefix)
        M_snprintf(buf, n, "ds%s", DEH_String(sfx->name));
    else
        M_StringCopy(buf, DEH_String(sfx->name), n);
}

static int Sfx_GetSfxLumpNum(sfxinfo_t *sfx)
{
    char name[9];
    sfx_lump_name(sfx, name, sizeof(name));
    return W_GetNumForName(name);
}

static const sample_t *load_sfx(sfxinfo_t *sfx)
{
    // DMX sound lump: u16 format (3), u16 rate, u32 length, 16 padding bytes, samples, 16 padding bytes
    byte *data;
    unsigned lumplen, length, rate;
    sample_t *s;
    if (sfx->driver_data)
        return (const sample_t *)sfx->driver_data;
    if (sfx->lumpnum < 0)
        sfx->lumpnum = Sfx_GetSfxLumpNum(sfx);
    data = W_CacheLumpNum(sfx->lumpnum, PU_STATIC);
    lumplen = W_LumpLength(sfx->lumpnum);
    if (lumplen < 8 || data[0] != 3 || data[1] != 0) {
        W_ReleaseLumpNum(sfx->lumpnum);
        return NULL;
    }
    rate = (data[3] << 8) | data[2];
    length = ((unsigned)data[7] << 24) | (data[6] << 16) | (data[5] << 8) | data[4];
    if (length > lumplen - 8 || length <= 48 || rate == 0) {
        W_ReleaseLumpNum(sfx->lumpnum);
        return NULL;
    }
    s = (sample_t *)malloc(sizeof(sample_t));
    s->len = length - 32;
    s->rate = rate;
    s->data = (uint8_t *)malloc(s->len);
    memcpy(s->data, data + 8 + 16, s->len);
    W_ReleaseLumpNum(sfx->lumpnum);
    sfx->driver_data = s;
    return s;
}

static void set_params(channel_t *ch, int vol, int sep)
{
    // Chocolate Doom's panning: sep 0 = left .. 254 = right, vol 0..127
    if (sep < 0)
        sep = 0;
    if (sep > 254)
        sep = 254;
    if (vol < 0)
        vol = 0;
    if (vol > 127)
        vol = 127;
    ch->left = ((254 - sep) * vol) / 127;
    ch->right = (sep * vol) / 127;
    if (ch->left > 255)
        ch->left = 255;
    if (ch->right > 255)
        ch->right = 255;
}

static void Sfx_Update(void) {}

static void Sfx_UpdateSoundParams(int channel, int vol, int sep)
{
    if (!s_sfx_ok || channel < 0 || channel >= NCHAN)
        return;
    EnterCriticalSection(&s_mix_lock);
    set_params(&s_chan[channel], vol, sep);
    LeaveCriticalSection(&s_mix_lock);
}

static int Sfx_StartSound(sfxinfo_t *sfx, int channel, int vol, int sep)
{
    const sample_t *s;
    if (!s_sfx_ok || channel < 0 || channel >= NCHAN)
        return -1;
    s = load_sfx(sfx);
    if (!s)
        return -1;
    EnterCriticalSection(&s_mix_lock);
    s_chan[channel].s = s;
    s_stat_sfx++;
    s_chan[channel].pos = 0;
    s_chan[channel].step = ((uint64_t)s->rate << 32) / RATE;
    set_params(&s_chan[channel], vol, sep);
    LeaveCriticalSection(&s_mix_lock);
    return channel;
}

static void Sfx_StopSound(int channel)
{
    if (!s_sfx_ok || channel < 0 || channel >= NCHAN)
        return;
    EnterCriticalSection(&s_mix_lock);
    s_chan[channel].s = NULL;
    LeaveCriticalSection(&s_mix_lock);
}

static boolean Sfx_SoundIsPlaying(int channel)
{
    if (!s_sfx_ok || channel < 0 || channel >= NCHAN)
        return false;
    return s_chan[channel].s != NULL;
}

static void Sfx_CacheSounds(sfxinfo_t *sounds, int num_sounds)
{
    int i;
    char name[9];
    for (i = 0; i < num_sounds; i++) {
        sfx_lump_name(&sounds[i], name, sizeof(name));
        sounds[i].lumpnum = W_CheckNumForName(name);
        if (sounds[i].lumpnum >= 0)
            load_sfx(&sounds[i]);
    }
}

static snddevice_t sfx_devices[] = {
    SNDDEVICE_SB, SNDDEVICE_PAS, SNDDEVICE_GUS, SNDDEVICE_WAVEBLASTER, SNDDEVICE_SOUNDCANVAS, SNDDEVICE_AWE32,
};

sound_module_t DG_sound_module = {
    sfx_devices,
    sizeof(sfx_devices) / sizeof(sfx_devices[0]),
    Sfx_Init,
    Sfx_Shutdown,
    Sfx_GetSfxLumpNum,
    Sfx_Update,
    Sfx_UpdateSoundParams,
    Sfx_StartSound,
    Sfx_StopSound,
    Sfx_SoundIsPlaying,
    Sfx_CacheSounds,
};

// ================================================================== music
typedef struct {
    uint64_t us;                                // time from the start of the song, microseconds
    uint32_t msg;                               // short MIDI message (status | d1 << 8 | d2 << 16)
} mevent_t;

typedef struct {
    mevent_t *ev;
    int count;
    uint64_t length_us;
} song_t;

static HMIDIOUT s_midi;
static HANDLE s_music_thread;
static CRITICAL_SECTION s_music_lock;
static song_t *s_song;                          // playing song (owned by RegisterSong/UnRegisterSong)
static int s_song_pos;
static boolean s_looping, s_music_paused, s_playing;
static uint64_t s_song_t0;                      // QPC microseconds at song time 0
static int s_music_volume = 100;                // 0..127 from DOOM
static int s_chan_vol[16];
static LARGE_INTEGER s_mqpf;
static volatile LONG s_was_paused;

static uint64_t now_us(void)
{
    LARGE_INTEGER t;
    QueryPerformanceCounter(&t);
    return (uint64_t)(t.QuadPart * 1000000 / s_mqpf.QuadPart);
}

static void midi_send(uint32_t msg)
{
    s_stat_midi++;
    if (s_midi && !NarewDoom_Silent())
        midiOutShortMsg(s_midi, msg);
}

static void send_volume(int ch)
{
    midi_send(0xb0 | ch | (7 << 8) | ((uint32_t)(s_chan_vol[ch] * s_music_volume / 127) << 16));
}

static void all_notes_off(void)
{
    int ch;
    for (ch = 0; ch < 16; ch++) {
        midi_send(0xb0 | ch | (123 << 8));          // all notes off
        midi_send(0xb0 | ch | (64 << 8));           // sustain off
    }
}

static void reset_channels(void)
{
    int ch;
    for (ch = 0; ch < 16; ch++) {
        s_chan_vol[ch] = 100;
        midi_send(0xb0 | ch | (121 << 8));          // reset controllers
        send_volume(ch);
    }
}

static void play_event(uint32_t msg)
{
    uint32_t st = msg & 0xf0, ch = msg & 0x0f;
    if (st == 0xb0 && ((msg >> 8) & 0x7f) == 7) {   // channel volume: scale by DOOM's music volume
        s_chan_vol[ch] = (msg >> 16) & 0x7f;
        send_volume((int)ch);
        return;
    }
    midi_send(msg);
}

static DWORD WINAPI music_thread(LPVOID arg)
{
    (void)arg;
    timeBeginPeriod(1);
    for (;;) {
        boolean paused;
        EnterCriticalSection(&s_music_lock);
        paused = s_music_paused || s_paused;
        if (paused != (boolean)s_was_paused) {
            if (paused) {
                s_was_paused = 1;
                all_notes_off();
                s_song_t0 = now_us() - s_song_t0;  // keep the elapsed song time while paused
            } else {
                s_was_paused = 0;
                s_song_t0 = now_us() - s_song_t0;
            }
        }
        if (s_playing && s_song && !paused) {
            uint64_t t = now_us() - s_song_t0;
            while (s_song_pos < s_song->count && s_song->ev[s_song_pos].us <= t)
                play_event(s_song->ev[s_song_pos++].msg);
            if (s_song_pos >= s_song->count && t >= s_song->length_us) {
                if (s_looping) {
                    all_notes_off();
                    s_song_pos = 0;
                    s_song_t0 = now_us();
                } else {
                    s_playing = false;
                }
            }
        }
        LeaveCriticalSection(&s_music_lock);
        Sleep(1);
    }
}

static boolean Mus_Init(void)
{
    MMRESULT r = midiOutOpen(&s_midi, MIDI_MAPPER, 0, 0, CALLBACK_NULL);
    if (r != MMSYSERR_NOERROR) {
        NarewDoom_Log("music: midiOutOpen failed (%u)", r);
        s_midi = NULL;
        return false;
    }
    QueryPerformanceFrequency(&s_mqpf);
    InitializeCriticalSection(&s_music_lock);
    reset_channels();
    s_music_thread = CreateThread(NULL, 0, music_thread, NULL, 0, NULL);
    SetThreadPriority(s_music_thread, THREAD_PRIORITY_ABOVE_NORMAL);
    NarewDoom_Log("music: Windows MIDI");
    return true;
}

static void Mus_Shutdown(void) {}

static void Mus_SetMusicVolume(int volume)
{
    int ch;
    if (!s_midi)
        return;
    EnterCriticalSection(&s_music_lock);
    s_music_volume = volume < 0 ? 0 : volume > 127 ? 127 : volume;
    for (ch = 0; ch < 16; ch++)
        send_volume(ch);
    LeaveCriticalSection(&s_music_lock);
}

static void Mus_Pause(void)
{
    if (!s_midi)
        return;
    EnterCriticalSection(&s_music_lock);
    s_music_paused = true;
    LeaveCriticalSection(&s_music_lock);
}

static void Mus_Resume(void)
{
    if (!s_midi)
        return;
    EnterCriticalSection(&s_music_lock);
    s_music_paused = false;
    LeaveCriticalSection(&s_music_lock);
}

// ---- standard MIDI file -> sorted list of timed short messages
static uint32_t rd32(const uint8_t *p) { return ((uint32_t)p[0] << 24) | (p[1] << 16) | (p[2] << 8) | p[3]; }
static uint32_t rd16(const uint8_t *p) { return (p[0] << 8) | p[1]; }

typedef struct {
    uint64_t tick;
    uint32_t order;
    uint32_t msg;                               // 0xff000000 | tempo  for tempo changes
} rawev_t;

static int cmp_raw(const void *a, const void *b)
{
    const rawev_t *x = (const rawev_t *)a, *y = (const rawev_t *)b;
    if (x->tick != y->tick)
        return x->tick < y->tick ? -1 : 1;
    return x->order < y->order ? -1 : x->order > y->order;
}

static int read_vlq(const uint8_t *p, const uint8_t *end, uint32_t *out)
{
    uint32_t v = 0;
    int n = 0;
    while (p + n < end && n < 4) {
        v = (v << 7) | (p[n] & 0x7f);
        if (!(p[n++] & 0x80)) {
            *out = v;
            return n;
        }
    }
    return -1;
}

static song_t *parse_midi(const uint8_t *d, size_t len)
{
    uint32_t ntracks, division, t, order = 0;
    size_t pos, cap = 4096, n = 0;
    rawev_t *raw;
    song_t *song;
    uint64_t us = 0, last_tick = 0, end_tick = 0;
    uint32_t tempo = 500000;
    size_t i;
    if (len < 14 || memcmp(d, "MThd", 4) != 0 || rd32(d + 4) < 6)
        return NULL;
    ntracks = rd16(d + 10);
    division = rd16(d + 12);
    if (division == 0 || (division & 0x8000))
        division = 96;                          // SMPTE time: not used by DOOM music
    raw = (rawev_t *)malloc(cap * sizeof(rawev_t));
    pos = 8 + rd32(d + 4);
    for (t = 0; t < ntracks && pos + 8 <= len; t++) {
        const uint8_t *p, *end;
        uint64_t tick = 0;
        uint8_t status = 0;
        uint32_t tlen = rd32(d + pos + 4);
        if (memcmp(d + pos, "MTrk", 4) != 0 || pos + 8 + tlen > len)
            break;
        p = d + pos + 8;
        end = p + tlen;
        pos += 8 + tlen;
        while (p < end) {
            uint32_t delta, mlen;
            int k = read_vlq(p, end, &delta);
            if (k < 0)
                break;
            p += k;
            tick += delta;
            if (p >= end)
                break;
            if (*p & 0x80)
                status = *p++;
            if (status == 0xff) {               // meta event
                uint8_t type;
                if (p >= end)
                    break;
                type = *p++;
                k = read_vlq(p, end, &mlen);
                if (k < 0 || p + k + mlen > end)
                    break;
                p += k;
                if (type == 0x51 && mlen == 3) {
                    if (n == cap)
                        raw = (rawev_t *)realloc(raw, (cap *= 2) * sizeof(rawev_t));
                    raw[n].tick = tick;
                    raw[n].order = order++;
                    raw[n].msg = 0xff000000u | ((uint32_t)p[0] << 16) | (p[1] << 8) | p[2];
                    n++;
                }
                p += mlen;
                if (type == 0x2f)
                    break;
                continue;
            }
            if (status == 0xf0 || status == 0xf7) { // sysex: skip
                k = read_vlq(p, end, &mlen);
                if (k < 0 || p + k + mlen > end)
                    break;
                p += k + mlen;
                continue;
            }
            if (status < 0x80)
                break;
            {
                uint32_t msg = status, st = status & 0xf0;
                int nd = (st == 0xc0 || st == 0xd0) ? 1 : 2;
                if (p + nd > end)
                    break;
                msg |= (uint32_t)p[0] << 8;
                if (nd == 2)
                    msg |= (uint32_t)p[1] << 16;
                p += nd;
                if (n == cap)
                    raw = (rawev_t *)realloc(raw, (cap *= 2) * sizeof(rawev_t));
                raw[n].tick = tick;
                raw[n].order = order++;
                raw[n].msg = msg;
                n++;
            }
        }
        if (tick > end_tick)
            end_tick = tick;
    }
    qsort(raw, n, sizeof(rawev_t), cmp_raw);
    song = (song_t *)calloc(1, sizeof(song_t));
    song->ev = (mevent_t *)malloc((n + 1) * sizeof(mevent_t));
    for (i = 0; i < n; i++) {
        us += (raw[i].tick - last_tick) * tempo / division;
        last_tick = raw[i].tick;
        if ((raw[i].msg & 0xff000000u) == 0xff000000u) {
            tempo = raw[i].msg & 0xffffff;
            continue;
        }
        song->ev[song->count].us = us;
        song->ev[song->count].msg = raw[i].msg;
        song->count++;
    }
    song->length_us = us + (end_tick > last_tick ? (end_tick - last_tick) * tempo / division : 0);
    free(raw);
    return song;
}

static void *Mus_RegisterSong(void *data, int len)
{
    song_t *song = NULL;
    if (!s_midi)
        return NULL;
    if (len >= 4 && memcmp(data, "MThd", 4) == 0) {
        song = parse_midi((const uint8_t *)data, (size_t)len);
    } else {
        MEMFILE *in = mem_fopen_read(data, len), *out = mem_fopen_write();
        if (!mus2mid(in, out)) {
            void *buf;
            size_t blen;
            mem_get_buf(out, &buf, &blen);
            song = parse_midi((const uint8_t *)buf, blen);
        }
        mem_fclose(in);
        mem_fclose(out);
    }
    if (!song)
        NarewDoom_Log("music: could not read a song (%d bytes)", len);
    return song;
}

static void Mus_UnRegisterSong(void *handle)
{
    song_t *song = (song_t *)handle;
    if (!song)
        return;
    EnterCriticalSection(&s_music_lock);
    if (s_song == song) {
        s_song = NULL;
        s_playing = false;
        all_notes_off();
    }
    LeaveCriticalSection(&s_music_lock);
    free(song->ev);
    free(song);
}

static void Mus_PlaySong(void *handle, boolean looping)
{
    if (!s_midi || !handle)
        return;
    EnterCriticalSection(&s_music_lock);
    all_notes_off();
    reset_channels();
    s_song = (song_t *)handle;
    s_stat_songs++;
    s_song_pos = 0;
    s_looping = looping;
    s_playing = true;
    s_song_t0 = s_was_paused ? 0 : now_us();    // while paused s_song_t0 holds the elapsed time
    LeaveCriticalSection(&s_music_lock);
}

static void Mus_StopSong(void)
{
    if (!s_midi)
        return;
    EnterCriticalSection(&s_music_lock);
    s_playing = false;
    all_notes_off();
    LeaveCriticalSection(&s_music_lock);
}

static boolean Mus_IsPlaying(void)
{
    return s_playing;
}

static void Mus_Poll(void) {}

static snddevice_t music_devices[] = {
    SNDDEVICE_PAS, SNDDEVICE_GUS, SNDDEVICE_WAVEBLASTER, SNDDEVICE_SOUNDCANVAS, SNDDEVICE_GENMIDI,
    SNDDEVICE_AWE32, SNDDEVICE_SB, SNDDEVICE_ADLIB,
};

music_module_t DG_music_module = {
    music_devices,
    sizeof(music_devices) / sizeof(music_devices[0]),
    Mus_Init,
    Mus_Shutdown,
    Mus_SetMusicVolume,
    Mus_Pause,
    Mus_Resume,
    Mus_RegisterSong,
    Mus_UnRegisterSong,
    Mus_PlaySong,
    Mus_StopSong,
    Mus_IsPlaying,
    Mus_Poll,
};

// ================================================================== pause (DOOM off screen)
void NarewSound_SetPaused(int paused)
{
    s_paused = paused ? 1 : 0;                  // the mixer outputs silence, the sequencer holds its place
}

const char *NarewSound_Stats(void)
{
    static char buf[160];
    _snprintf_s(buf, sizeof(buf), _TRUNCATE, "sfx_ok=%d sfx_started=%ld mix_buffers=%ld midi_ok=%d songs=%ld midi_msgs=%ld",
                (int)s_sfx_ok, s_stat_sfx, s_stat_buffers, s_midi != NULL, s_stat_songs, s_stat_midi);
    return buf;
}
