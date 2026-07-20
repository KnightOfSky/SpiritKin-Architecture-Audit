package com.spiritkin.mobilelinkbridge;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.content.Context;
import android.database.Cursor;
import android.database.MatrixCursor;
import android.net.Uri;
import android.os.ParcelFileDescriptor;
import android.provider.OpenableColumns;
import java.io.File;
import java.io.FileNotFoundException;

public class CacheFileProvider extends ContentProvider {
    static final String AUTHORITY = "com.spiritkin.mobilelinkbridge.files";

    static Uri uriForFile(Context context, File file) {
        return new Uri.Builder()
                .scheme("content")
                .authority(AUTHORITY)
                .appendPath(file.getName())
                .build();
    }

    @Override
    public boolean onCreate() {
        return true;
    }

    @Override
    public String getType(Uri uri) {
        String name = safeName(uri);
        String lower = name.toLowerCase();
        if (lower.endsWith(".png")) {
            return "image/png";
        }
        if (lower.endsWith(".webp")) {
            return "image/webp";
        }
        if (lower.endsWith(".gif")) {
            return "image/gif";
        }
        if (lower.endsWith(".jpg") || lower.endsWith(".jpeg")) {
            return "image/jpeg";
        }
        if (lower.endsWith(".apk")) {
            return "application/vnd.android.package-archive";
        }
        return "application/octet-stream";
    }

    @Override
    public Cursor query(Uri uri, String[] projection, String selection, String[] selectionArgs, String sortOrder) {
        File file = resolve(uri);
        MatrixCursor cursor = new MatrixCursor(new String[] {OpenableColumns.DISPLAY_NAME, OpenableColumns.SIZE});
        cursor.addRow(new Object[] {file.getName(), file.exists() ? file.length() : 0});
        return cursor;
    }

    @Override
    public Uri insert(Uri uri, ContentValues values) {
        throw new UnsupportedOperationException("insert is not supported");
    }

    @Override
    public int delete(Uri uri, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("delete is not supported");
    }

    @Override
    public int update(Uri uri, ContentValues values, String selection, String[] selectionArgs) {
        throw new UnsupportedOperationException("update is not supported");
    }

    @Override
    public ParcelFileDescriptor openFile(Uri uri, String mode) throws FileNotFoundException {
        if (!"r".equals(mode) && !"rt".equals(mode)) {
            throw new FileNotFoundException("read only");
        }
        File file = resolve(uri);
        if (!file.exists() || !file.isFile()) {
            throw new FileNotFoundException(file.getName());
        }
        return ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY);
    }

    private File resolve(Uri uri) {
        Context context = getContext();
        File root = context == null ? new File(".") : new File(context.getCacheDir(), "spiritkin-artifacts");
        return new File(root, safeName(uri));
    }

    private String safeName(Uri uri) {
        String name = uri == null ? "" : uri.getLastPathSegment();
        if (name == null || name.trim().isEmpty()) {
            name = "artifact.bin";
        }
        return name.replace("\\", "_").replace("/", "_").replace("..", "_");
    }
}
