import { PageHeader } from "@/components/layout/page-header";
import { UploadWorkspace } from "@/features/datasets/upload/upload-workspace";

export default function UploadPage() {
  return (
    <div>
      <PageHeader
        eyebrow="Data foundation"
        title="Upload a dataset"
        description="CSV, TSV and Excel files are validated and profiled automatically after upload."
        backHref="/datasets"
        backLabel="Datasets"
      />
      <UploadWorkspace />
    </div>
  );
}
