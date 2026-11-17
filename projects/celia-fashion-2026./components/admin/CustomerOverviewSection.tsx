"use client"

import * as React from "react"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Checkbox } from "@/components/ui/checkbox"
import AsteriskIcon from "@/components/icons/asterisk.svg"
import AvatarPlaceholderIcon from "@/components/icons/avatar-placeholder.svg"
import USFlagIcon from "@/components/icons/us-flag.svg"

interface CustomerOverviewSectionProps {
  data: {
    firstName: string
    lastName: string
    language: string
    phoneNumber: string
    marketingEmails: boolean
    smsMarketing: boolean
    avatar: File | null
  }
  onUpdate: (data: any) => void
  onNext: () => void
  onSaveAsDraft: () => void
}

export function CustomerOverviewSection({ data, onUpdate, onNext, onSaveAsDraft }: CustomerOverviewSectionProps) {
  const handleInputChange = (field: string, value: any) => {
    onUpdate({ [field]: value })
  }

  return (
    <div className="pt-6 flex flex-col gap-6">
      {/* Header */}
      <div className="flex items-center gap-3">
        <div className="w-3 h-6 rounded bg-[#cabdff]" />
        <h2 className="text-[24px] font-bold leading-[28px] tracking-[-0.24px] text-[#3f434a]">
          Customer Overview
        </h2>
      </div>

      {/* Form Fields */}
      <div className="flex flex-col gap-6">
        {/* Avatar Upload */}
        <div className="flex flex-col gap-1">
          <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
            Avatar
          </Label>
          <div className="flex items-center gap-5">
            <AvatarPlaceholderIcon className="w-14 h-14" />
            <div className="flex items-center gap-5">
              <button className="text-[16px] font-medium tracking-[0.16px] text-[#0b1234] hover:underline">
                Choose
              </button>
              <span className="text-[12px] tracking-[-0.24px] text-[#696f8c]">
                JPG, GIF or PNG. 1MB Max.
              </span>
            </div>
          </div>
        </div>

        {/* Name Fields Row */}
        <div className="flex gap-6">
          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-1">
              <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
                First Name
              </Label>
              <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
            </div>
            <Input
              value={data.firstName}
              onChange={(e) => handleInputChange("firstName", e.target.value)}
              placeholder="Jon"
            />
          </div>

          <div className="flex-1 flex flex-col gap-2">
            <div className="flex items-center gap-1">
              <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
                Last Name
              </Label>
              <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
            </div>
            <Input
              value={data.lastName}
              onChange={(e) => handleInputChange("lastName", e.target.value)}
              placeholder="Snow"
            />
          </div>
        </div>

        {/* Language Field */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1">
            <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
              Language
            </Label>
            <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
          </div>
          <Select value={data.language} onValueChange={(value: string) => handleInputChange("language", value)}>
            <SelectTrigger>
              <SelectValue placeholder="English (Default)" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="en">English (Default)</SelectItem>
              <SelectItem value="es">Spanish</SelectItem>
              <SelectItem value="fr">French</SelectItem>
            </SelectContent>
          </Select>
        </div>

        {/* Phone Number Field */}
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-1">
            <Label className="text-[14px] font-medium tracking-[-0.28px] text-[#364050]">
              Phone Number
            </Label>
            <AsteriskIcon className="w-[7px] h-[7px]" style={{ color: "#17a584" }} />
          </div>
          <div className="flex h-10 items-center rounded-lg border border-[#0b12341a] bg-white shadow-[0px_0px_0px_1px_rgba(11,18,52,0.10),0px_1px_2px_rgba(11,18,52,0.15)]">
            <div className="flex items-center gap-2 px-3 border-r border-[#0b12341a]">
              <USFlagIcon className="w-5 h-5 rounded-full" />
              <span className="text-[14px] text-[#0b1234]">+1</span>
              <svg className="w-3 h-3 text-[#696f8c]" fill="currentColor" viewBox="0 0 20 20">
                <path fillRule="evenodd" d="M5.293 7.293a1 1 0 011.414 0L10 10.586l3.293-3.293a1 1 0 111.414 1.414l-4 4a1 1 0 01-1.414 0l-4-4a1 1 0 010-1.414z" clipRule="evenodd" />
              </svg>
            </div>
            <input
              type="tel"
              value={data.phoneNumber}
              onChange={(e) => handleInputChange("phoneNumber", e.target.value)}
              placeholder="555‑0199"
              className="flex-1 px-3 text-[14px] text-[#696f8c] bg-transparent outline-none"
            />
          </div>
        </div>

        {/* Consent Checkboxes */}
        <div className="flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <Checkbox
              checked={data.marketingEmails}
              onCheckedChange={(checked: boolean) => handleInputChange("marketingEmails", checked)}
            />
            <label className="text-[12px] tracking-[-0.24px] text-[#696f8c]">
              Customer agreed to receive marketing emails.
            </label>
          </div>

          <div className="flex items-center gap-3">
            <Checkbox
              checked={data.smsMarketing}
              onCheckedChange={(checked: boolean) => handleInputChange("smsMarketing", checked)}
            />
            <label className="text-[12px] tracking-[-0.24px] text-[#696f8c]">
              Customer agreed to receive SMS marketing text messages.
            </label>
          </div>
        </div>
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
          Save and Next
        </button>
      </div>
    </div>
  )
}