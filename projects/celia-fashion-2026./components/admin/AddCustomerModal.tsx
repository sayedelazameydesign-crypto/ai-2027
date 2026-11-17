"use client"

import * as React from "react"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { cn } from "@/lib/utils"
import { CustomerOverviewSection } from "./CustomerOverviewSection"
import { CustomerAddressSection } from "./CustomerAddressSection"
import { CustomerNotesSection } from "./CustomerNotesSection"

interface AddCustomerModalProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function AddCustomerModal({ open, onOpenChange }: AddCustomerModalProps) {
  const [currentStep, setCurrentStep] = React.useState(0)
  const [formData, setFormData] = React.useState({
    overview: {
      firstName: "",
      lastName: "",
      language: "",
      phoneNumber: "",
      marketingEmails: false,
      smsMarketing: false,
      avatar: null as File | null,
    },
    address: {
      country: "",
      region: "",
      apartments: "",
      city: "",
      zipCode: "",
      phoneNumber: "",
    },
    notes: {
      note: "",
    },
  })

  const handleNext = () => {
    if (currentStep < 2) {
      setCurrentStep(currentStep + 1)
    } else {
      // Final submit
      console.log("Form submitted:", formData)
      onOpenChange(false)
      setCurrentStep(0)
    }
  }

  const handleSaveAsDraft = () => {
    console.log("Saved as draft:", formData)
    onOpenChange(false)
    setCurrentStep(0)
  }

  const updateFormData = (section: string, data: any) => {
    setFormData(prev => ({
      ...prev,
      [section]: { ...prev[section as keyof typeof prev], ...data }
    }))
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[600px] p-0 gap-0 rounded-2xl shadow-[0px_0px_0px_1px_rgba(11,18,52,0.15),0px_5px_11px_rgba(5,9,31,0.10),0px_21px_21px_rgba(5,9,31,0.09),0px_47px_28px_rgba(5,9,31,0.05),0px_83px_33px_rgba(5,9,31,0.01),0px_130px_36px_rgba(5,9,31,0.00)] font-[family-name:var(--font-inter)]">
        {/* Tabs */}
        <div className="flex items-center justify-center gap-0 border-b border-[#f0f0f0] px-6 pt-6">
          <TabButton
            active={currentStep === 0}
            onClick={() => setCurrentStep(0)}
            label="Overview"
          />
          <TabButton
            active={currentStep === 1}
            onClick={() => setCurrentStep(1)}
            label="Address"
          />
          <TabButton
            active={currentStep === 2}
            onClick={() => setCurrentStep(2)}
            label="Notes"
          />
        </div>

        {/* Content */}
        <div className="px-6 pb-6">
          {currentStep === 0 && (
            <CustomerOverviewSection
              data={formData.overview}
              onUpdate={(data) => updateFormData("overview", data)}
              onNext={handleNext}
              onSaveAsDraft={handleSaveAsDraft}
            />
          )}
          {currentStep === 1 && (
            <CustomerAddressSection
              data={formData.address}
              onUpdate={(data) => updateFormData("address", data)}
              onNext={handleNext}
              onSaveAsDraft={handleSaveAsDraft}
            />
          )}
          {currentStep === 2 && (
            <CustomerNotesSection
              data={formData.notes}
              onUpdate={(data) => updateFormData("notes", data)}
              onNext={handleNext}
              onSaveAsDraft={handleSaveAsDraft}
            />
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}

interface TabButtonProps {
  active: boolean
  onClick: () => void
  label: string
}

function TabButton({ active, onClick, label }: TabButtonProps) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "relative flex-1 pb-3 text-center text-[14px] font-medium tracking-[-0.28px] transition-colors",
        active ? "text-[#0f62fe]" : "text-[#696f8c]"
      )}
    >
      {label}
      {active && (
        <div className="absolute bottom-0 left-0 right-0 h-[3px] bg-[#0f62fe]" />
      )}
    </button>
  )
}