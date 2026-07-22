import { UploadDropzone } from "@/components/upload-dropzone";

export default function UploadPage() {
  return (
    <section className="space-y-4">
      <h1 className="text-xl font-semibold">上传简历</h1>
      <UploadDropzone />
    </section>
  );
}
