// ProcessLoopbackCapture.cs
// Нативный захват звука ОДНОГО процесса (Windows 10 build 20348+ / Win11).
// Нужен потому, что Python не может безопасно реализовать COM-колбэк
// ActivateAudioInterfaceAsync: vtable вручную падает с access violation,
// а comtypes не отдаёт указатель на свой объект.
// В C# CCW сам управляет vtable, поэтому здесь это работает стабильно.
//
// API: ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
//      IID_IAudioClient, PROPVARIANT(AUDIOCLIENT_ACTIVATION_PARAMS),
//      IActivateAudioInterfaceCompletionHandler, &operation)
//      затем IActivateAudioInterfaceAsyncOperation::GetResults -> IAudioClient.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace ScreenOcr
{
    // ── COM-интерфейсы (объявлены вручную: typelib mmdevapi недоступен) ──
    [ComImport, Guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAgileObject
    {
    }

    [ComImport, Guid("41D949AB-9862-444A-80F6-C261334DA5EB"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IActivateAudioInterfaceCompletionHandler : IAgileObject
    {
        [PreserveSig] int ActivateCompleted(IntPtr hr);
    }

    [ComImport, Guid("72A22D78-CDE4-431B-B8CC-843A71199A6D"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IActivateAudioInterfaceAsyncOperation
    {
        [PreserveSig] int GetResults(ref Guid riid, out IntPtr ppv);
    }

    [ComImport, Guid("1EA69A41-DDB8-4C09-A347-6DAFFC9F0DBC"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioClient
    {
        int Initialize(int ShareMode, uint StreamFlags, long hnsBufferDuration,
                       long hnsPeriodicity, ref WAVEFORMATEX pFormat,
                       ref Guid AudioSessionGuid);
        int GetBufferSize(out uint pNumBufferFrames);
        int GetStreamLatency(out long phnsLatency);
        int GetCurrentPadding(out uint pNumPaddingFrames);
        int IsFormatSupported(int ShareMode, ref WAVEFORMATEX pFormat,
                              out IntPtr ppClosestMatch);
        int GetMixFormat(out IntPtr ppDeviceFormat);
        int GetDevicePeriod(out long phnsDefaultDevicePeriod,
                            out long phnsMinimumDevicePeriod);
        int Start();
        int Stop();
        int Reset();
        int SetEventHandle(IntPtr eventHandle);
        int GetService(ref Guid riid, out IntPtr ppv);
    }

    [ComImport, Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IAudioCaptureClient
    {
        int GetBuffer(out IntPtr ppData, out uint pNumFramesToRead,
                      out uint pdwFlags, out ulong pu64DevicePosition,
                      out ulong pu64QPCPosition);
        int ReleaseBuffer(uint NumFramesRead);
        int GetNextPacketSize(out uint pNumFramesInNextPacket);
        int GetDevicePeriod(out long phnsDefaultDevicePeriod,
                            out long phnsMinimumDevicePeriod);
        int GetDevicePosition(out ulong pu64DevicePosition,
                              out ulong pu64QPCPosition);
        int GetAdapterDevicePosition(out ulong pu64DevicePosition,
                                     out ulong pu64QPCPosition);
    }

    [StructLayout(LayoutKind.Sequential, Pack = 2)]
    internal struct WAVEFORMATEX
    {
        public ushort wFormatTag;
        public ushort nChannels;
        public uint nSamplesPerSec;
        public uint nAvgBytesPerSec;
        public ushort nBlockAlign;
        public ushort wBitsPerSample;
        public ushort cbSize;
    }

    // Плоская раскладка PROPVARIANT под x64 (проверено: union в этой
    // сборке ctypes выравнивался неверно, здесь то же самое):
    //   vt(0..2) wReserved1..3(2..8) cbSize(8..12) pad(12..16) pBlobData(16..24)
    [StructLayout(LayoutKind.Sequential)]
    internal struct PROPVARIANT
    {
        public ushort vt;
        public ushort wReserved1;
        public ushort wReserved2;
        public ushort wReserved3;
        public uint blobSize;
        public uint pad;
        public IntPtr pBlobData;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS
    {
        public uint ProcessId;
        public int IncludeProcessTree;
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct AUDIOCLIENT_ACTIVATION_PARAMS
    {
        public int ActivationType;                       // 1 = PROCESS_LOOPBACK
        public AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS Loop;
    }

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    internal delegate int ActivateCompletedDelegate(IntPtr self, int hr);

    /// <summary>
    /// Захват аудио одного процесса. Данные отдаются в callback
    /// (mono float32) — так Python получает ровно то, что уже усреднено.
    /// </summary>
    public class ProcessLoopbackCapture : IDisposable
    {
        // Аудио формат: float32, 48 кГц, 2 канала (требование WASAPI
        // process loopback), затем усредняем каналы в моно.
        private const int WAVE_FORMAT_IEEE_FLOAT = 3;
        private const int AUDCLNT_SHAREMODE_SHARED = 0;
        private const int AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000;
        private const int AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1;
        private const int VT_BLOB = 65;
        private const int S_OK = 0;

        private const string VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK =
            @"VAD\Process_Loopback";

        // Callback: (float[] mono, int sampleRate) — Samples приходят с
        // шага 16000, чтобы Python не пересэмплировал.
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        public delegate void AudioCallback(IntPtr data, int frames, int srcRate);

        private static readonly Guid IID_IAudioClient =
            new Guid("1EA69A41-DDB8-4C09-A347-6DAFFC9F0DBC");

        private uint _pid;
        private AudioCallback _cb;
        private Thread _thread;
        private volatile bool _running;
        private volatile bool _started;
        private IAudioClient _client;
        private IAudioCaptureClient _capture;
        private int _channels = 2;
        private int _srcRate = 48000;
        private long _samples = 0;          // счётчик пакетов
        private string _lastError = null;

        public string LastError { get { return _lastError; } }
        public bool IsActive
        {
            get { return _started && _running && _thread != null
                           && _thread.IsAlive; }
        }
        public long SamplesReceived { get { return Interlocked.Read(ref _samples); } }
        public int SampleRate { get { return _srcRate; } }

        public ProcessLoopbackCapture(uint pid, AudioCallback cb)
        {
            _pid = pid;
            _cb = cb;
        }

        public void Start()
        {
            if (_running) return;
            _running = true;
            _thread = new Thread(_Run);
            _thread.IsBackground = true;
            _thread.SetApartmentState(ApartmentState.MTA);
            _thread.Start();
        }

        public void Dispose()
        {
            _running = false;
            try { if (_client != null) { _client.Stop(); } }
            catch { }
        }

        // ── Реализация колбэка активации ──
        // ВАЖНО: объект колбэка сделан вручную (unmanaged vtable), а не
        // через CCW. CCW в .NET Framework для этого интерфейса передаёт
        // в ActivateCompleted мусор вместо HRESULT, и активация падает.
        // Здесь вызовы идут напрямую, без маршалинга .NET.
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate int QueryInterfaceFn(IntPtr self, IntPtr riid,
                                              out IntPtr ppv);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate uint AddRefFn(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate uint ReleaseFn(IntPtr self);
        [UnmanagedFunctionPointer(CallingConvention.StdCall)]
        private delegate int ActivateCompletedFn(IntPtr self, int hr);

        // Держим delegate-объекты живыми: GC убрал бы их — и vtable сломался бы
        private static readonly List<object> _keepAlive =
            new List<object>();

        private static readonly Guid _iidIUnknown =
            new Guid("00000000-0000-0000-C000-000000000046");
        private static readonly Guid _iidIAgileObject =
            new Guid("94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90");
        private static readonly Guid _iidCompletion =
            new Guid("41D949AB-9862-444A-80F6-C261334DA5EB");
        private const int E_NOINTERFACE = unchecked((int)0x80004002);

        private IntPtr _cbObj;
        private GCHandle _cbState;

        private sealed class CbState
        {
            public int Hr = int.MinValue;
            public IntPtr Operation = IntPtr.Zero;
            public readonly ManualResetEventSlim Done =
                new ManualResetEventSlim(false);
        }

        private IntPtr _MakeCompletionHandler(CbState st)
        {
            QueryInterfaceFn qi = delegate(IntPtr self, IntPtr riid,
                                           out IntPtr ppv)
            {
                // Поддерживаем IUnknown, IAgileObject и сам колбэк:
                // mmdevapi требует IAgileObject, иначе E_INVALIDARG.
                Guid iid = (Guid)Marshal.PtrToStructure(riid, typeof(Guid));
                ppv = IntPtr.Zero;
                if (iid.Equals(_iidIUnknown) || iid.Equals(_iidIAgileObject)
                    || iid.Equals(_iidCompletion))
                {
                    ppv = self;
                    return 0;
                }
                return E_NOINTERFACE;
            };
            AddRefFn addRef = delegate(IntPtr self) { return 1; };
            ReleaseFn rel = delegate(IntPtr self) { return 1; };
            ActivateCompletedFn done = delegate(IntPtr self, int hr)
            {
                st.Hr = hr;
                st.Done.Set();
                return 0;
            };

            IntPtr vtbl;
            IntPtr obj;
            try
            {
                vtbl = Marshal.AllocHGlobal(IntPtr.Size * 4);
                Marshal.WriteIntPtr(vtbl, 0 * IntPtr.Size,
                    Marshal.GetFunctionPointerForDelegate(qi));
                Marshal.WriteIntPtr(vtbl, 1 * IntPtr.Size,
                    Marshal.GetFunctionPointerForDelegate(addRef));
                Marshal.WriteIntPtr(vtbl, 2 * IntPtr.Size,
                    Marshal.GetFunctionPointerForDelegate(rel));
                Marshal.WriteIntPtr(vtbl, 3 * IntPtr.Size,
                    Marshal.GetFunctionPointerForDelegate(done));
                _keepAlive.Add(qi);
                _keepAlive.Add(addRef);
                _keepAlive.Add(rel);
                _keepAlive.Add(done);

                obj = Marshal.AllocHGlobal(IntPtr.Size * 2);
                Marshal.WriteIntPtr(obj, 0, vtbl);
                GCHandle tmp = GCHandle.Alloc(st, GCHandleType.Pinned);
                Marshal.WriteIntPtr(obj, IntPtr.Size, tmp.AddrOfPinnedObject());
                tmp.Free();
            }
            catch (Exception e)
            {
                throw new InvalidOperationException(
                    "build vtable/obj: " + e.GetType().Name + " / " + e.Message);
            }
            _cbState = GCHandle.Alloc(st);
            _cbObj = obj;
            return obj;
        }

        private void _Run()
        {
            try
            {
                CbState act = new CbState();
                IntPtr handler;
                try
                {
                    handler = _MakeCompletionHandler(act);
                }
                catch (Exception e)
                {
                    throw new InvalidOperationException(
                        "MakeCompletionHandler: " + e.Message + " / "
                        + e.GetType().Name);
                }

                AUDIOCLIENT_ACTIVATION_PARAMS ap = new AUDIOCLIENT_ACTIVATION_PARAMS();
                ap.ActivationType = AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
                ap.Loop.ProcessId = _pid;
                ap.Loop.IncludeProcessTree = 1;

                int pSize = Marshal.SizeOf(typeof(AUDIOCLIENT_ACTIVATION_PARAMS));
                IntPtr blob = Marshal.AllocHGlobal(pSize);
                try
                {
                    Marshal.StructureToPtr(ap, blob, false);

                    PROPVARIANT pv = new PROPVARIANT();
                    pv.vt = VT_BLOB;
                    pv.blobSize = (uint)pSize;
                    pv.pad = 0;
                    pv.pBlobData = blob;

                    // ref нельзя применить к static readonly — нужна копия
                    Guid iidClientLocal = IID_IAudioClient;
                    IntPtr opPtr;
                    int hr;
                    try
                    {
                        hr = ActivateAudioInterfaceAsync(
                            VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
                            ref iidClientLocal, ref pv, handler, out opPtr);
                    }
                    catch (Exception e)
                    {
                        throw new InvalidOperationException(
                            "ActivateAudioInterfaceAsync pinvoke: "
                            + e.GetType().Name + " / " + e.Message);
                    }
                    if (hr != S_OK)
                        throw new InvalidOperationException(
                            "ActivateAudioInterfaceAsync failed: 0x"
                            + hr.ToString("X8"));
                    act.Operation = opPtr;
                }
                finally { Marshal.FreeHGlobal(blob); }

                if (!act.Done.Wait(TimeSpan.FromSeconds(10)))
                    throw new TimeoutException("ActivateCompleted timeout");
                if (act.Hr != S_OK)
                    throw new InvalidOperationException(
                        "activation hr: 0x" + act.Hr.ToString("X8"));
                if (act.Operation == IntPtr.Zero)
                    throw new InvalidOperationException("operation is NULL");

                // IAudioClient через GetResults
                // IntPtr нельзя привести к ComImport-интерфейсу напрямую —
                // нужен GetObjectForIUnknown
                IActivateAudioInterfaceAsyncOperation op =
                    (IActivateAudioInterfaceAsyncOperation)
                    Marshal.GetObjectForIUnknown(act.Operation);
                IntPtr ppv;
                Guid iidClient = IID_IAudioClient;
                int hrGet = op.GetResults(ref iidClient, out ppv);
                Marshal.Release(act.Operation);
                if (hrGet != S_OK || ppv == IntPtr.Zero)
                    throw new InvalidOperationException(
                        "GetResults failed: 0x" + hrGet.ToString("X8"));

                _client = (IAudioClient)Marshal.GetObjectForIUnknown(ppv);
                Marshal.Release(ppv);

                WAVEFORMATEX fmt = new WAVEFORMATEX();
                fmt.wFormatTag = WAVE_FORMAT_IEEE_FLOAT;
                fmt.nChannels = 2;
                fmt.nSamplesPerSec = 48000;
                fmt.wBitsPerSample = 32;
                fmt.nBlockAlign = (ushort)(fmt.nChannels * 4);
                fmt.nAvgBytesPerSec = fmt.nSamplesPerSec * fmt.nBlockAlign;
                fmt.cbSize = 0;
                Guid sessionGuid = Guid.Empty;

                int hrInit = _client.Initialize(
                    AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_LOOPBACK,
                    0, 0, ref fmt, ref sessionGuid);
                if (hrInit != S_OK)
                    throw new InvalidOperationException(
                        "Initialize failed: 0x" + hrInit.ToString("X8"));

                Guid iidCap = new Guid("C8ADBD64-E71E-48A0-A4DE-185C395CD317");
                IntPtr capPtr;
                int hrSvc = _client.GetService(ref iidCap, out capPtr);
                if (hrSvc != S_OK)
                    throw new InvalidOperationException(
                        "GetService failed: 0x" + hrSvc.ToString("X8"));
                _capture = (IAudioCaptureClient)Marshal.GetObjectForIUnknown(capPtr);
                Marshal.Release(capPtr);

                _channels = fmt.nChannels;
                _srcRate = (int)fmt.nSamplesPerSec;

                int hrStart = _client.Start();
                if (hrStart != S_OK)
                    throw new InvalidOperationException(
                        "Start failed: 0x" + hrStart.ToString("X8"));
                _started = true;

                _Pump();
            }
            catch (Exception e)
            {
                _lastError = e.Message;
                _started = false;
            }
            finally
            {
                _running = false;
                _started = false;
            }
        }

        // Чтение пакетов: усредняем 2 канала в моно и зовём callback.
        private void _Pump()
        {
            while (_running)
            {
                uint avail;
                if (_capture.GetNextPacketSize(out avail) != S_OK || avail == 0)
                {
                    Thread.Sleep(5);
                    continue;
                }

                IntPtr data;
                uint frames;
                uint flags;
                ulong devPos, qpcPos;
                if (_capture.GetBuffer(out data, out frames, out flags,
                                       out devPos, out qpcPos) != S_OK
                    || frames == 0)
                {
                    Thread.Sleep(5);
                    continue;
                }
                try
                {
                    int n = (int)frames * _channels;
                    float[] buf = new float[n];
                    Marshal.Copy(data, buf, 0, n);
                    if (_channels > 1)
                    {
                        for (int i = 0; i < frames; i++)
                            buf[i] = (buf[i * 2] + buf[i * 2 + 1]) * 0.5f;
                    }
                    if (buf.Length > 0)
                    {
                        IntPtr p = Marshal.AllocHGlobal(buf.Length * 4);
                        try
                        {
                            Marshal.Copy(buf, 0, p, buf.Length);
                            _cb(p, buf.Length, _srcRate);
                        }
                        finally { Marshal.FreeHGlobal(p); }
                    }
                    Interlocked.Increment(ref _samples);
                }
                finally
                {
                    _capture.ReleaseBuffer(frames);
                }
            }
        }

        // Пятый out-параметр нам нужен как «сырой» указатель: через
        // GetObjectForIUnknown сразу отдаём его в GetResults.
        [DllImport("mmdevapi.dll", CharSet = CharSet.Unicode,
                   EntryPoint = "ActivateAudioInterfaceAsync")]
        private static extern int ActivateAudioInterfaceAsync(
            [MarshalAs(UnmanagedType.LPWStr)] string deviceInterfacePath,
            ref Guid riid, ref PROPVARIANT activationParams,
            IntPtr completionHandler, out IntPtr activationOperation);
    }
}
