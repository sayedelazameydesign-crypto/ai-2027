"use client"

import * as React from "react"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import EditIcon from "@/components/icons/edit.svg"

interface CustomerNotesSectionProps {
  data: {
    note: string
  }
  onUpdate: (data: any) => void
  onNext: () => void
  onSaveAsDraft: () => void
}

export function CustomerNotesSection({ data, onUpdate, onNext, onSaveAsDraft }: CustomerNotesSectionProps) {
  const handleInputChange = (field: string, value: any) => {
    onUpdate({ [field]: value })
  }

  return (
    <div className="pt-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-3 h-6 rounded bg-[#ff9d8f]" />
        <h2 className="text-[24px] font-bold leading-[28px] tracking-[-0.24px] text-[#3f434a]">
          Notes
        </h2>
      </div>

      {/* Form Fields */}
      <div className="flex flex-col gap-2">
        <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
          Note
        </Label>
        
        <div className="relative">
          <Textarea
            value={data.note}
            onChange={(e) => handleInputChange("note", e.target.value)}
            placeholder="A short and clear note for your customer..."
            className="min-h-[202px] pr-10"
          />
          <EditIcon 
            className="absolute bottom-3 right-3 w-[9px] h-[9px]" 
            style={{ color: "#696f8c" }}
          />
        </div>

        <p className="text-[14px] tracking-[-0.28px] text-[#364050] mt-2">
          Add personal note for your customer
        </p>
      </div>

      {/* Action Buttons */}
      <div className="flex items-center justify-between pt-4">
        <button
          onClick={onSaveAsDraft}
          className="text-[16px] font-medium tracking-[0.16px] text-[#0b1234] hover:underline"
        >
          Save as Draft
        </button>
        <button
          onClick={onNext}
          className="px-6 py-2 rounded-lg bg-[#0f62fe] text-[16px] font-medium tracking-[0.16px] text-white hover:bg-[#0f62fe]/90 transition-colors"
        >
          Done
        </button>
      </div>
    </div>
  )
}