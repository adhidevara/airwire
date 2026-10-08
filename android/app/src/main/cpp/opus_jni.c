// JNI bridge for com.wifiaudio.streamer.codec.OpusNative.
// The encoder handle is the OpusEncoder* cast to jlong; one handle per streaming thread.

#include <jni.h>
#include <stdint.h>
#include <opus.h>

#define FN(name) Java_com_wifiaudio_streamer_codec_OpusNative_##name

static OpusEncoder *enc_of(jlong handle) {
    return (OpusEncoder *) (intptr_t) handle;
}

JNIEXPORT jlong JNICALL
FN(create)(JNIEnv *env, jclass clazz, jint sample_rate, jint channels, jint application,
           jint bitrate, jint complexity, jboolean inband_fec, jint packet_loss_perc) {
    (void) env;
    (void) clazz;
    int err = OPUS_OK;
    OpusEncoder *enc = opus_encoder_create(sample_rate, channels, application, &err);
    if (err != OPUS_OK || enc == NULL) {
        return 0;
    }
    opus_encoder_ctl(enc, OPUS_SET_BITRATE(bitrate));
    opus_encoder_ctl(enc, OPUS_SET_COMPLEXITY(complexity));
    opus_encoder_ctl(enc, OPUS_SET_INBAND_FEC(inband_fec ? 1 : 0));
    opus_encoder_ctl(enc, OPUS_SET_PACKET_LOSS_PERC(packet_loss_perc));
    return (jlong) (intptr_t) enc;
}

JNIEXPORT jint JNICALL
FN(encode)(JNIEnv *env, jclass clazz, jlong handle, jshortArray pcm, jint frame_size,
           jbyteArray out) {
    (void) clazz;
    OpusEncoder *enc = enc_of(handle);
    if (enc == NULL) {
        return OPUS_BAD_ARG;
    }
    jsize out_cap = (*env)->GetArrayLength(env, out);
    jshort *in = (*env)->GetPrimitiveArrayCritical(env, pcm, NULL);
    if (in == NULL) {
        return OPUS_ALLOC_FAIL;
    }
    jbyte *dst = (*env)->GetPrimitiveArrayCritical(env, out, NULL);
    if (dst == NULL) {
        (*env)->ReleasePrimitiveArrayCritical(env, pcm, in, JNI_ABORT);
        return OPUS_ALLOC_FAIL;
    }
    opus_int32 n = opus_encode(enc, in, frame_size, (unsigned char *) dst, out_cap);
    (*env)->ReleasePrimitiveArrayCritical(env, out, dst, 0);
    (*env)->ReleasePrimitiveArrayCritical(env, pcm, in, JNI_ABORT);
    return n;
}

JNIEXPORT jint JNICALL
FN(setBitrate)(JNIEnv *env, jclass clazz, jlong handle, jint bitrate) {
    (void) env;
    (void) clazz;
    OpusEncoder *enc = enc_of(handle);
    return enc == NULL ? OPUS_BAD_ARG : opus_encoder_ctl(enc, OPUS_SET_BITRATE(bitrate));
}

JNIEXPORT jint JNICALL
FN(getLookahead)(JNIEnv *env, jclass clazz, jlong handle) {
    (void) env;
    (void) clazz;
    OpusEncoder *enc = enc_of(handle);
    opus_int32 lookahead = 0;
    if (enc == NULL || opus_encoder_ctl(enc, OPUS_GET_LOOKAHEAD(&lookahead)) != OPUS_OK) {
        return 0;
    }
    return lookahead;
}

JNIEXPORT void JNICALL
FN(destroy)(JNIEnv *env, jclass clazz, jlong handle) {
    (void) env;
    (void) clazz;
    OpusEncoder *enc = enc_of(handle);
    if (enc != NULL) {
        opus_encoder_destroy(enc);
    }
}
