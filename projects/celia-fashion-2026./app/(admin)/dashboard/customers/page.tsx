"use client"

import { useState } from "react"
import { AddCustomerModal } from "@/components/admin/AddCustomerModal"

export default function CustomersPage() {
  const [isModalOpen, setIsModalOpen] = useState(false)

  return (
    <div className="container mx-auto py-10">
      <div className="flex items-center justify-between mb-8">
        <h1 className="text-3xl font-bold">Customers</h1>
        <button
          onClick={() => setIsModalOpen(true)}
          className="px-4 py-2 bg-[#0f62fe] text-white rounded-lg hover:bg-[#0f62fe]/90 transition-colors"
        >
          Add New Customer
        </button>
      </div>

      <div className="bg-white rounded-lg shadow p-6">
        <p className="text-gray-600">Click "Add New Customer" to open the modal.</p>
      </div>

      <AddCustomerModal open={isModalOpen} onOpenChange={setIsModalOpen} />
    </div>
  )
}