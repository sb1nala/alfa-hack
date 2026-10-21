import type { ApiClient } from './client.ts';
import type { components } from './schema.d.ts';

type UploadBody = components['schemas']['Body_upload_document_api_applications__application_id__documents_post'];
export type UploadDocument = Omit<UploadBody, 'file'> & { file: File };

/** OpenAPI представляет binary как string; сериализуем фактический File в FormData. */
export function uploadDocument(api: ApiClient, applicationId: string, body: UploadDocument, signal?: AbortSignal) {
  return api.http.POST('/api/applications/{application_id}/documents', {
    params: { path: { application_id: applicationId } },
    body: { ...body, file: body.file.name },
    bodySerializer: (metadata) => {
      const form = new FormData();
      form.append('file', body.file);
      form.append('document_type', metadata.document_type);
      form.append('expected_revision', String(metadata.expected_revision));
      return form;
    },
    signal,
  });
}
